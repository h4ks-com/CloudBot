import logging
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from cloudbot.bot import bot
from cloudbot.util import database
from cloudbot.webhooks.handlers import call_webhook_handler
from cloudbot.webhooks.plugin_parser import PluginParser
from plugins.core.webhook_tokens import (
    cleanup_expired_tokens,
    is_token_valid,
    verify_webhook_signature,
)

logger = logging.getLogger("cloudbot.webhooks")

router = APIRouter()
security = HTTPBearer()

# Setup Jinja2 templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Plugin parsing configuration
COMMAND_BLACKLIST = {
    "admin",
    "permissions",
    "factoids",
    "ignore",
    "chan_track",
    "history",
    "logs",
    "help",
    "reload",
    "eval",
    "raw",
    "quit",
    "restart",
    "part",
    "join",
    "nick",
    "mode",
    "kick",
    "ban",
}

BROKEN_PLUGINS = {
    "spellcheck",
    "tvdb",
    "yandex_translate",
    "core",
}


class HealthResponse(BaseModel):
    status: str


class SendMessageRequest(BaseModel):
    message: str
    target: str


class SendMessageResponse(BaseModel):
    status: str
    detail: str | None = None


def authenticate(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    token = credentials.credentials
    bot_instance = bot.get()

    # Check temporary webhook tokens first
    db = database.Session()
    try:
        cleanup_expired_tokens(db)
        if is_token_valid(db, token):
            logger.info(
                "Webhook authentication successful with temporary token"
            )
            return "temporary"
    finally:
        db.close()

    # Check configured static tokens
    webhooks_config = bot_instance.config.get("webhooks", {})
    tokens = webhooks_config.get("tokens", {})

    for token_name, expected_token in tokens.items():
        if secrets.compare_digest(token.encode(), expected_token.encode()):
            logger.info(
                "Webhook authentication successful with token: %s", token_name
            )
            return token_name

    raise HTTPException(status_code=401, detail="Invalid token")


def get_repo_link() -> str:
    """Get repository link from bot config."""
    bot_instance = bot.get()
    if bot_instance:
        return bot_instance.config.get(
            "repo_link", "https://github.com/h4ks-com/CloudBot"
        )
    return "https://github.com/h4ks-com/CloudBot"


def get_command_prefix() -> str:
    """Get command prefix from bot config."""
    bot_instance = bot.get()
    if bot_instance:
        connections = bot_instance.config.get("connections", [])
        if connections:
            # Get command_prefix from first connection
            return connections[0].get("command_prefix", ".")
    return "."


def get_plugins_directory() -> str:
    """Get the plugins directory path."""
    bot_instance = bot.get()
    if bot_instance:
        bot_dir = Path(bot_instance.base_dir)
        return str(bot_dir / "plugins")
    # Fallback to relative path
    current_dir = Path(__file__).parent.parent.parent
    return str(current_dir / "plugins")


def _parse_plugins():
    """Parse all plugins using shared configuration."""
    plugins_dir = get_plugins_directory()
    parser = PluginParser(
        plugins_dir=plugins_dir,
        blacklist=COMMAND_BLACKLIST,
        broken_plugins=BROKEN_PLUGINS,
    )
    return parser.parse_all_plugins(), plugins_dir


def get_all_commands_data_for_json() -> list[dict]:
    """Generate commands data for JSON API with GitHub URLs."""
    plugins, plugins_dir = _parse_plugins()
    repo_link = get_repo_link()

    # Flatten commands from all plugins
    all_commands = []
    for plugin in plugins:
        for command in plugin.commands:
            # Convert file path to relative path for GitHub linking
            relative_path = os.path.relpath(command.file_path, plugins_dir)

            # Generate GitHub URL
            github_url = f"{repo_link}/blob/master/plugins/{relative_path}"
            if command.line_number:
                github_url += f"#L{command.line_number}"

            command_dict = {
                "name": command.name,
                "aliases": command.aliases,
                "function_name": command.function_name,
                "docstring": command.docstring,
                "plugin_name": command.plugin_name,
                "status": command.status,
                "github_url": github_url,
            }
            all_commands.append(command_dict)

    # Sort commands alphabetically
    all_commands.sort(key=lambda x: x["name"])
    return all_commands


def get_all_commands_data() -> list[dict]:
    """Generate commands data for web interface with separate file_path and line_number."""
    plugins, plugins_dir = _parse_plugins()

    # Flatten commands from all plugins
    all_commands = []
    for plugin in plugins:
        for command in plugin.commands:
            # Convert file path to relative path for GitHub linking
            relative_path = os.path.relpath(command.file_path, plugins_dir)

            command_dict = {
                "name": command.name,
                "aliases": command.aliases,
                "function_name": command.function_name,
                "docstring": command.docstring,
                "file_path": f"plugins/{relative_path}",
                "line_number": command.line_number,
                "plugin_name": command.plugin_name,
                "status": command.status,
            }
            all_commands.append(command_dict)

    # Sort commands alphabetically
    all_commands.sort(key=lambda x: x["name"])
    return all_commands


@router.get("/", response_class=HTMLResponse)
def documentation_page(request: Request) -> HTMLResponse:
    """Serve the main documentation page with all CloudBot commands."""
    all_commands = get_all_commands_data()

    return templates.TemplateResponse(
        "documentation.html",
        {
            "request": request,
            "commands": all_commands,
            "repo_link": get_repo_link(),
            "plugins_dir": get_plugins_directory(),
            "command_prefix": get_command_prefix(),
        },
    )


@router.get("/plugins.json", response_class=JSONResponse)
def plugins_json() -> JSONResponse:
    """Return all plugin commands data as JSON."""
    all_commands = get_all_commands_data_for_json()

    response_data = {
        "commands": all_commands,
        "total_commands": len(all_commands),
        "functional_commands": len(
            [cmd for cmd in all_commands if cmd["status"] == "functional"]
        ),
        "broken_commands": len(
            [cmd for cmd in all_commands if cmd["status"] == "broken"]
        ),
        "repo_link": get_repo_link(),
        "command_prefix": get_command_prefix(),
        "plugins": list({cmd["plugin_name"] for cmd in all_commands}),
    }

    return JSONResponse(content=response_data)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post("/send_message", response_model=SendMessageResponse)
def send_message(
    request: SendMessageRequest, token_name: str = Depends(authenticate)
) -> SendMessageResponse:
    bot_instance = bot.get()
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not available")
    connection = bot_instance.connections.get("gobot")
    if not connection:
        return SendMessageResponse(
            status="error", detail="Connection not found"
        )
    if not connection.connected:
        return SendMessageResponse(
            status="error", detail="Not connected to IRC"
        )
    if not request.target or not request.target.strip():
        return SendMessageResponse(status="error", detail="Invalid target")
    if not request.message or not request.message.strip():
        return SendMessageResponse(status="error", detail="Invalid message")
    connection.message(request.target, request.message)
    return SendMessageResponse(
        status="sent",
        detail="Message sent. IRC errors (e.g., invalid target) are logged but not returned here.",
    )


@router.post("/webhooks/{plugin_name}")
async def receive_webhook(plugin_name: str, request: Request):
    """Receive and process webhook events for registered plugins."""
    payload = await request.json()
    signature = request.headers.get("X-Webhook-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing signature header")

    bot_instance = bot.get()
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not available")

    subscriptions = bot_instance.config.get("webhooks", {}).get(
        "subscriptions", []
    )
    signing_key = None
    for sub in subscriptions:
        if sub.get("plugin") == plugin_name:
            signing_key = sub.get("signing_key")
            break

    if not signing_key:
        raise HTTPException(
            status_code=404, detail=f"Plugin {plugin_name} not configured"
        )

    if not verify_webhook_signature(payload, signature, signing_key):
        logger.warning("Invalid webhook signature for plugin: %s", plugin_name)
        raise HTTPException(status_code=401, detail="Invalid signature")

    logger.info("Webhook received for plugin: %s", plugin_name)
    call_webhook_handler(plugin_name, bot_instance, payload)
    return {"status": "received"}
