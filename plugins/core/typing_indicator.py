"""
IRCv3 typing notifications for command hooks
"""

from cloudbot import hook
from cloudbot.hook import Priority
from cloudbot.util.typing import (
    cleanup_typing_for_connection,
    start_typing_for_command,
    stop_typing_for_command,
)


@hook.sieve(priority=Priority.HIGHEST)
async def typing_start_sieve(bot, event, _hook):
    """Send typing=active before executing command hooks"""
    if _hook.type != "command":
        return event

    target = event.chan or event.nick
    if not target:
        return event

    typing_enabled = event.conn.config.get("typing_notifications", True)
    if not typing_enabled:
        return event

    command_id = id(event)
    await start_typing_for_command(event.conn, target, command_id)
    return event


@hook.post_hook(priority=Priority.HIGHEST)
async def typing_end_hook(result, error, launched_event, launched_hook):
    """Send typing=done after command completes"""
    if launched_hook.type != "command":
        return

    target = launched_event.chan or launched_event.nick
    if not target:
        return

    typing_enabled = launched_event.conn.config.get("typing_notifications", True)
    if not typing_enabled:
        return

    command_id = id(launched_event)
    await stop_typing_for_command(launched_event.conn, target, command_id)


@hook.on_stop()
async def cleanup_on_disconnect(conn):
    """Clean up typing state when connection stops"""
    await cleanup_typing_for_connection(conn.name)
