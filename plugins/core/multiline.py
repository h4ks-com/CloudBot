"""
IRCv3 draft/multiline capability negotiation
"""

import logging

from cloudbot import hook

logger = logging.getLogger("cloudbot")


@hook.on_cap_available("draft/multiline")
def request_multiline_cap():
    """Request draft/multiline when server advertises it"""
    return True


@hook.on_cap_ack("draft/multiline")
def on_multiline_ack(conn):
    """Log when draft/multiline is enabled"""
    logger.info("[%s] draft/multiline capability enabled", conn.name)
