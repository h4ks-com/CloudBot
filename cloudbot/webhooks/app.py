import logging
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from cloudbot.bot import bot

logger = logging.getLogger("cloudbot.webhooks")

app = FastAPI()


class HealthResponse(BaseModel):
    status: str


class SendMessageRequest(BaseModel):
    message: str
    target: str


class SendMessageResponse(BaseModel):
    status: str
    detail: str | None = None


def is_enabled() -> bool:
    """Check if webhooks are enabled in config."""
    bot_instance = bot.get()
    if bot_instance:
        webhooks_config = bot_instance.config.get("webhooks", {})
        return webhooks_config.get("enabled", False)
    return False


def get_port() -> int:
    """Get the configured port for webhooks."""
    bot_instance = bot.get()
    if bot_instance:
        webhooks_config = bot_instance.config.get("webhooks", {})
        return webhooks_config.get("port", 8080)
    return 8080


def authenticate(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())) -> str:
    token = credentials.credentials
    webhooks_config = bot.get().config.get("webhooks", {})
    tokens = webhooks_config.get("tokens", {})

    for token_name, expected_token in tokens.items():
        if secrets.compare_digest(token.encode(), expected_token.encode()):
            logger.info("Webhook authentication successful with token: %s", token_name)
            return token_name

    raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/send_message", response_model=SendMessageResponse)
def send_message(request: SendMessageRequest, token_name: str = Depends(authenticate)) -> SendMessageResponse:
    bot_instance = bot.get()
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not available")
    connection = bot_instance.connections.get("gobot")
    if not connection:
        return SendMessageResponse(status="error", detail="Connection not found")
    if not connection.connected:
        return SendMessageResponse(status="error", detail="Not connected to IRC")
    if not request.target or not request.target.strip():
        return SendMessageResponse(status="error", detail="Invalid target")
    if not request.message or not request.message.strip():
        return SendMessageResponse(status="error", detail="Invalid message")
    try:
        connection.message(request.target, request.message)
        return SendMessageResponse(
            status="sent", detail="Message sent. IRC errors (e.g., invalid target) are logged but not returned here."
        )
    except Exception as e:
        return SendMessageResponse(status="error", detail=str(e))


def setup_app():
    """Setup the app - routes are already defined above."""
    pass

