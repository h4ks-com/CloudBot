import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("cloudbot.webhooks")

webhook_handlers: dict[str, Callable[[Any, dict[str, Any]], None]] = {}


def register_webhook_handler(
    plugin_name: str, handler: Callable[[Any, dict[str, Any]], None]
) -> None:
    """Register a webhook handler for a plugin."""
    webhook_handlers[plugin_name] = handler
    logger.info("Registered webhook handler for plugin: %s", plugin_name)


def call_webhook_handler(plugin_name: str, bot: Any, payload: dict[str, Any]) -> None:
    """Call the webhook handler for a plugin if registered."""
    handler = webhook_handlers.get(plugin_name)
    if handler:
        handler(bot, payload)
