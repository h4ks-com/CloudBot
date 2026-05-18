"""
IRCv3 typing notification utilities with async state management
"""

import asyncio
import time
from dataclasses import dataclass, field

TYPING_TIMEOUT = 180
TYPING_INTERVAL = 5


@dataclass
class ChannelTypingState:
    active_commands: set[int] = field(default_factory=set)
    background_task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    start_time: float = field(default_factory=time.time)


typing_state: dict[tuple[str, str], ChannelTypingState] = {}
typing_state_lock = asyncio.Lock()


def supports_typing(conn) -> bool:
    """Check if connection supports typing client tag

    +typing is a client-only tag, so it only requires message-tags capability
    """
    server_caps = conn.memory.get("server_caps", {})
    return bool(server_caps.get("message-tags", False))


def _send_typing_active(conn, target: str) -> None:
    """Send typing=active notification to target"""
    if not supports_typing(conn):
        return
    conn.send(f"@+typing=active TAGMSG {target}", log=False)


def _send_typing_done(conn, target: str) -> None:
    """Send typing=done notification to target"""
    if not supports_typing(conn):
        return
    conn.send(f"@+typing=done TAGMSG {target}", log=False)


async def _typing_sender_loop(
    conn, target: str, state: ChannelTypingState
) -> None:
    """Background task that sends typing notifications periodically"""
    try:
        while True:
            if time.time() - state.start_time > TYPING_TIMEOUT:
                break

            _send_typing_active(conn, target)
            await asyncio.sleep(TYPING_INTERVAL)
    except asyncio.CancelledError:
        pass
    finally:
        _send_typing_done(conn, target)


async def start_typing_for_command(conn, target: str, command_id: int) -> None:
    """Start or increment typing notifications for a target (channel or user)"""
    if not supports_typing(conn):
        return

    key = (conn.name, target)

    async with typing_state_lock:
        if key not in typing_state:
            state = ChannelTypingState()
            typing_state[key] = state
            state.background_task = asyncio.create_task(
                _typing_sender_loop(conn, target, state)
            )

        state = typing_state[key]
        async with state.lock:
            state.active_commands.add(command_id)


async def stop_typing_for_command(conn, target: str, command_id: int) -> None:
    """Stop or decrement typing notifications for a target"""
    if not supports_typing(conn):
        return

    key = (conn.name, target)

    async with typing_state_lock:
        if key not in typing_state:
            return

        state = typing_state[key]
        async with state.lock:
            state.active_commands.discard(command_id)

            if not state.active_commands and state.background_task:
                state.background_task.cancel()
                try:
                    await state.background_task
                except asyncio.CancelledError:
                    pass

                del typing_state[key]


async def cleanup_typing_for_connection(conn_name: str) -> None:
    """Clean up all typing state for a connection"""
    async with typing_state_lock:
        keys_to_remove = [key for key in typing_state if key[0] == conn_name]

        for key in keys_to_remove:
            state = typing_state[key]
            if state.background_task:
                state.background_task.cancel()
                try:
                    await state.background_task
                except asyncio.CancelledError:
                    pass

            del typing_state[key]
