"""List the streams that are live on a MediaMTX server.

Config (in config.json under "streams"):
  {
    "streams": {
      "api_url": "https://live.example.com/v3/paths/list",
      "watch_url": "https://live.example.com"
    }
  }
"""

from collections.abc import Callable
from typing import TypedDict

import aiohttp

from cloudbot import hook
from cloudbot.bot import CloudBot

_TIMEOUT = aiohttp.ClientTimeout(total=8.0)
_MAX_PATHS = 1000


class PathItem(TypedDict):
    name: str
    ready: bool


class PathList(TypedDict):
    items: list[PathItem]


def live_paths(listing: PathList) -> list[str]:
    return [item["name"] for item in listing["items"] if item["ready"]]


@hook.command("streams", autohelp=False)
async def streams_cmd(
    bot: CloudBot, message: Callable[[str], None]
) -> str | None:
    """- list the streams that are live right now"""
    cfg = bot.config.get("streams", {})
    api_url = cfg.get("api_url")
    if not api_url:
        return "set streams.api_url in the config first"
    watch_url = cfg.get("watch_url", "").rstrip("/")

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(
                api_url, params={"itemsPerPage": _MAX_PATHS}
            ) as response:
                response.raise_for_status()
                listing = await response.json()
    except (aiohttp.ClientError, TimeoutError) as error:
        return f"could not reach the stream server: {error}"

    paths = live_paths(listing)
    if not paths:
        return "no streams live"
    for path in paths:
        message(f"{watch_url}/{path}")
    return None
