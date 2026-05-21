"""
IRCv3 batch and draft/multiline capability negotiation
"""

import logging

from cloudbot import hook

logger = logging.getLogger("cloudbot")


@hook.on_cap_available("batch")
def request_batch_cap():
    return True


@hook.on_cap_ack("batch")
def on_batch_ack(conn):
    logger.info("[%s] batch capability enabled", conn.name)


@hook.on_cap_available("draft/multiline")
def request_multiline_cap():
    return True


@hook.on_cap_ack("draft/multiline")
def on_multiline_ack(conn):
    logger.info("[%s] draft/multiline capability enabled", conn.name)
