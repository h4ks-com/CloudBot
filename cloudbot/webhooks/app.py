import logging

from fastapi import FastAPI

from cloudbot.bot import bot
from cloudbot.webhooks import routers

logger = logging.getLogger("cloudbot.webhooks")

app = FastAPI(
    title="CloudBot Webhooks API",
    description="API for interacting with CloudBot via webhooks, including sending messages and health checks.",
)

# Include the routers
app.include_router(routers.router)


def is_enabled() -> bool:
    """Check if webhooks are enabled in config."""
    bot_instance = bot.get()
    if bot_instance:
        webhooks_config = bot_instance.config.get("webhooks", {})
        return bool(webhooks_config.get("enabled", False))
    return False


def get_port() -> int:
    """Get the configured port for webhooks."""
    bot_instance = bot.get()
    if bot_instance:
        webhooks_config = bot_instance.config.get("webhooks", {})
        return int(webhooks_config.get("port", 8080))
    return 8080


def setup_app() -> None:
    """Setup the app - routes are already defined above."""
