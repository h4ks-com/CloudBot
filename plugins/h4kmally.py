"""sigmally — h4kmally agar.io clone server plugin.

Commands:
  .sigmally              — server overview: ping, players, bots
  .sigmally who          — real players online with scores
  .sigmally top          — top player by score
  .sigmally bots         — bots currently on the server
  .sigmally watch        — spectators
  .sigmally skins        — skins manifest summary by rarity
"""

import asyncio
import time

import aiohttp

from cloudbot import hook

_BASE_URL = "https://api.sigmally.h4ks.com"
_WS_URL = "wss://api.sigmally.h4ks.com/ws/"
_HANDSHAKE = b"SIG 0.0.2\x00"
_TIMEOUT = 8.0

_OP_BORDER = 64
_OP_PING = 254


def _fmt_score(score: int) -> str:
    if score >= 1_000_000:
        return f"{score / 1_000_000:.1f}M"
    if score >= 10_000:
        return f"{round(score / 1_000)}K"
    if score >= 1_000:
        return f"{score / 1_000:.1f}K"
    return str(score)


async def _fetch_status() -> dict:
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{_BASE_URL}/api/status") as resp:
            resp.raise_for_status()
            return await resp.json()


async def _do_probe() -> float | None:
    headers = {"Origin": "https://sigmally.h4ks.com"}
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(_WS_URL, headers=headers) as ws:
            await ws.send_bytes(_HANDSHAKE)

            msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
            if msg.type != aiohttp.WSMsgType.BINARY:
                return None

            raw: bytes = msg.data
            null_pos = raw.index(b"\x00")
            shuffle_start = null_pos + 1
            if len(raw) < shuffle_start + 256:
                return None

            forward = raw[shuffle_start : shuffle_start + 256]
            inverse = bytearray(256)
            for i, b in enumerate(forward):
                inverse[b] = i

            ping_sent_at: float | None = None
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
                logical_op = inverse[msg.data[0]]
                if logical_op == _OP_BORDER and ping_sent_at is None:
                    ping_sent_at = time.monotonic()
                    await ws.send_bytes(bytes([forward[_OP_PING]]))
                elif logical_op == _OP_PING and ping_sent_at is not None:
                    return (time.monotonic() - ping_sent_at) * 1000
    return None


async def _probe_ping() -> float | None:
    """WebSocket ping via SIG 0.0.2 protocol. Returns ms or None on any failure."""
    try:
        return await asyncio.wait_for(_do_probe(), timeout=_TIMEOUT)
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ConnectionError,
        ValueError,
    ):
        return None


async def _fetch_skins() -> str:
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{_BASE_URL}/api/skins") as resp:
                resp.raise_for_status()
                skins = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
        return f"\x02sigmally skins\x02: API error: {e}"

    if not isinstance(skins, list):
        return "\x02sigmally skins\x02: Unexpected API response."

    total = len(skins)
    rarities: dict[str, int] = {}
    for s in skins:
        r = s.get("rarity", "?")
        rarities[r] = rarities.get(r, 0) + 1

    rarity_str = " | ".join(f"{k}: {v}" for k, v in sorted(rarities.items()))
    summary = f" ({rarity_str})" if rarity_str else ""
    return f"\x02Skins ({total})\x02{summary} — https://sigmally.h4ks.com"


@hook.command("sigmally", "agar", autohelp=False)
async def sigmally_cmd(text, reply):
    """[who|top|bots|watch|skins] - sigmally agar.io server info"""
    sub = (text or "").strip().lower()

    if sub == "skins":
        return await _fetch_skins()

    if sub and sub not in ("who", "top", "bots", "watch"):
        return f"\x02sigmally\x02: Unknown subcommand '{sub}'. Try: who, top, bots, watch, skins"

    # All other subcommands (and no-arg) need the HTTP status
    ping_task = None
    if not sub:
        # Kick off WS ping concurrently only for the overview
        ping_task = asyncio.create_task(_probe_ping())

    try:
        status = await _fetch_status()
    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
        if ping_task:
            ping_task.cancel()
        reply(f"\x02sigmally\x02: Unable to connect: {e}")
        return

    if not sub:
        assert ping_task is not None
        ping_ms = await ping_task
        ping_str = f"{ping_ms:.0f}ms" if ping_ms is not None else "timeout"

        player_count = status.get("playerCount", 0)
        bot_count = status.get("botCount", 0)
        spec_count = status.get("spectatorCount", 0)

        filled = min(player_count, 10)
        progress_bar = "■" * filled + "□" * (10 - filled)

        parts = [
            "\x02Online\x02: https://sigmally.h4ks.com",
            f"\x02Ping\x02: {ping_str}",
            f"\x02Players\x02: {player_count} [{progress_bar}]",
            f"\x02Bots\x02: {bot_count}",
            f"\x02Spectators\x02: {spec_count}",
        ]
        return "\x0f" + " | ".join(parts)

    match sub:
        case "who":
            players = [
                p for p in status.get("players", []) if not p.get("isBot")
            ]
            if not players:
                return "\x02sigmally\x02: No players online right now."
            ranked = sorted(
                players, key=lambda p: p.get("score", 0), reverse=True
            )
            parts = []
            for p in ranked:
                name = p["name"]
                score = _fmt_score(p.get("score", 0))
                clan = p.get("clan", "")
                tag = f"[{clan}] " if clan else ""
                parts.append(f"{tag}\x02{name}\x02 ({score})")
            return "\x02Players\x02: " + " | ".join(parts)

        case "top":
            players = [
                p for p in status.get("players", []) if not p.get("isBot")
            ]
            if not players:
                return "\x02sigmally\x02: No players online right now."
            top = max(players, key=lambda p: p.get("score", 0))
            name = top["name"]
            score = _fmt_score(top.get("score", 0))
            cells = top.get("cells", 1)
            skin = top.get("skin", "")
            effect = top.get("effect", "")
            clan = top.get("clan", "")
            total = status.get("playerCount", len(players))

            extras = []
            if clan:
                extras.append(f"clan: \x02{clan}\x02")
            if skin:
                extras.append(f"skin: {skin}")
            if effect:
                extras.append(f"effect: {effect}")
            if cells > 1:
                extras.append(f"{cells} cells")
            extra_str = " — " + ", ".join(extras) if extras else ""

            return f"\x02#1\x02 \x02{name}\x02: {score} pts out of {total} players{extra_str}"

        case "bots":
            bots = status.get("bots", [])
            count = status.get("botCount", len(bots))
            if not bots:
                return f"\x02sigmally\x02: {count} bot(s) online."
            names = [b["name"] for b in bots]
            shown = names[:8]
            suffix = f" (+{len(names) - 8} more)" if len(names) > 8 else ""
            return f"\x02Bots ({count})\x02: {', '.join(shown)}{suffix}"

        case "watch":
            specs = status.get("spectators", [])
            count = status.get("spectatorCount", len(specs))
            if not specs:
                return f"\x02sigmally\x02: {count} spectator(s), none listed."
            names = [s.get("name", "?") for s in specs]
            return f"\x02Spectators ({count})\x02: {', '.join(names)}"
