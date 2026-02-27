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

# Track which event IDs have typing started for them
_typing_events: set[int] = set()


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
    _typing_events.add(command_id)
    await start_typing_for_command(event.conn, target, command_id)
    return event


@hook.post_hook(priority=Priority.HIGHEST)
async def typing_end_hook(result, error, launched_event, launched_hook):
    """Send typing=done after command completes or sieve aborts"""
    command_id = id(launched_event)

    if command_id not in _typing_events:
        return

    # Only stop typing when:
    # 1. A command hook completes (success or error)
    # 2. A sieve returns None (result is None means aborted)
    should_stop = False

    if launched_hook.type == "command":
        # Command completed (either success or error)
        should_stop = True
    elif launched_hook.type == "sieve" and result is None:
        # Sieve aborted execution
        should_stop = True

    if not should_stop:
        return

    _typing_events.discard(command_id)

    target = launched_event.chan or launched_event.nick
    if not target:
        return

    typing_enabled = launched_event.conn.config.get("typing_notifications", True)
    if not typing_enabled:
        return

    await stop_typing_for_command(launched_event.conn, target, command_id)


@hook.on_stop()
async def cleanup_on_disconnect(conn):
    """Clean up typing state when connection stops"""
    _typing_events.clear()
    if conn:
        await cleanup_typing_for_connection(conn.name)
