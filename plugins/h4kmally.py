"""h4kmally game server status plugin.

Reports ping and active player count by connecting to the h4kmally WebSocket
server and speaking the SIG 0.0.2 binary protocol.
"""

import asyncio
import struct
import time
from typing import Optional

import aiohttp

from cloudbot import hook

_SERVER_URL = "wss://api.sigmally.h4ks.com/ws/"
_HANDSHAKE = b"SIG 0.0.2\x00"
_TIMEOUT = 8.0

# SIG 0.0.2 logical opcodes (both sides use 254 for ping/reply)
_OP_BORDER = 64
_OP_LEADERBOARD = 49
_OP_PING = 254


async def _probe() -> dict:
    """Connect to h4kmally, return ping_ms (float|None) and players (int|None).

    Protocol flow:
      1. Send handshake string
      2. Receive version + 256-byte shuffle table
      3. Wait for BORDER (server ready signal), then send PING
      4. Collect PING_REPLY (latency) and LEADERBOARD (player count)
    """
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT)
    headers = {"Origin": "https://one.sigmally.com"}

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            _SERVER_URL, headers=headers, timeout=timeout
        ) as ws:
            await ws.send_bytes(_HANDSHAKE)

            msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
            if msg.type != aiohttp.WSMsgType.BINARY:
                raise ConnectionError(f"Expected binary frame, got {msg.type}")

            raw: bytes = msg.data
            null_pos = raw.index(b"\x00")
            shuffle_start = null_pos + 1
            if len(raw) < shuffle_start + 256:
                raise ConnectionError("Handshake too short: missing shuffle table")

            # Server sends forward shuffle table: wire_byte = forward[logical_op].
            # Client decodes received opcodes via inverse: logical_op = inverse[wire_byte].
            forward = raw[shuffle_start : shuffle_start + 256]
            inverse = bytearray(256)
            for i, b in enumerate(forward):
                inverse[b] = i

            ping_sent_at: Optional[float] = None
            ping_ms: Optional[float] = None
            leaderboard_count: Optional[int] = None
            deadline = time.monotonic() + _TIMEOUT

            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    msg = await asyncio.wait_for(
                        ws.receive(), timeout=max(0.1, remaining)
                    )
                except asyncio.TimeoutError:
                    break

                if msg.type != aiohttp.WSMsgType.BINARY or not msg.data:
                    break

                data: bytes = msg.data
                logical_op = inverse[data[0]]
                payload = data[1:]

                if logical_op == _OP_BORDER and ping_sent_at is None:
                    # BORDER is the server's ready signal; send our ping now
                    ping_sent_at = time.monotonic()
                    await ws.send_bytes(bytes([forward[_OP_PING]]))

                elif logical_op == _OP_PING and ping_sent_at is not None:
                    ping_ms = (time.monotonic() - ping_sent_at) * 1000

                elif logical_op == _OP_LEADERBOARD and len(payload) >= 4:
                    leaderboard_count = struct.unpack_from("<I", payload, 0)[0]

                if ping_ms is not None and leaderboard_count is not None:
                    break

    return {"ping_ms": ping_ms, "players": leaderboard_count}


@hook.command("sigmally", "h4kmally", "agar", autohelp=False)
async def h4kmally_status(reply):
    """- shows h4kmally agar.io server status: ping and active player count"""
    try:
        stats = await _probe()
    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
        reply(f"\x02h4kmally\x02: Unable to connect: {e}")
        raise

    ping = stats["ping_ms"]
    players = stats["players"]

    ping_str = f"{ping:.0f}ms" if ping is not None else "timeout"
    if players is None:
        player_str = "unknown"
    elif players == 1:
        player_str = "1 player"
    else:
        player_str = f"{players} players"

    parts = [
        "\x02Online\x02: https://sigmally.h4ks.com",
        f"\x02Ping\x02: {ping_str}",
        f"\x02Players\x02: {player_str}",
    ]
    return "\x0f" + " | ".join(parts)
