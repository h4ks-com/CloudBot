"""
IRCv3 typing capability negotiation

The +typing tag is a client-only tag that only requires message-tags capability.
Client-only tags use the + prefix and don't require separate capability negotiation.
"""

import logging

from cloudbot import hook

logger = logging.getLogger("cloudbot")


@hook.on_cap_available("message-tags")
def request_message_tags():
    """Request message-tags capability (required for client-only tags like +typing)"""
    return True


@hook.on_cap_ack("message-tags")
def on_message_tags_ack(conn):
    """Log when message-tags capability is enabled"""
    logger.info(
        "[%s] message-tags capability enabled (enables +typing client tag)",
        conn.name,
    )
