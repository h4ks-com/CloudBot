"""
google.py

Reimplementation using self-hosted SearXNG JSON API.

Requires:
    - searxng_url in config (e.g. https://searx.h4ks.com)

Maintains compatibility with original !gse and !gseis commands.

License:
    GNU General Public License (Version 3)
"""

import isodate

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util import formatting, timeformat
from cloudbot.util.web import get_session
from plugins.youtube import get_client, get_video_id, get_video_info

last_gse_url: dict[tuple[str, str], str] = {}

SEARCHXNG_URL = "https://searx.h4ks.com"


def searx_search(query: str, category: str = "general"):
    base_url = bot.config.get_api_key("searxng_url") or SEARCHXNG_URL

    if not base_url:
        return None, "Missing searxng_url in configuration."

    url = f"{base_url.rstrip('/')}/search"

    params = {
        "q": query,
        "format": "json",
        "categories": category,
    }

    try:
        response = get_session().get(url, params=params, timeout=10)
        response.raise_for_status()
        parsed = response.json()
    except Exception as e:
        return None, f"SearXNG error: {e}"

    results = parsed.get("results")
    if not results:
        return None, "No results found."

    return results[0], None


@hook.command("gse")
def gse(text: str, nick: str, chan: str) -> str:
    """<query> - Returns first search result using SearXNG."""

    result, error = searx_search(text, category="general")
    if error:
        return error

    url = result.get("url")
    title = formatting.truncate_str(result.get("title", "No title"), 60)
    content = result.get("content", "No description available.")

    if content:
        content = formatting.truncate_str(content.replace("\n", ""), 150)
    else:
        content = "No description available."

    if not url:
        return "No results found."

    # YouTube duration support
    video_id = get_video_id(url)
    if video_id:
        last_gse_url[(chan, nick)] = url
        try:
            client = get_client()
            video_info = get_video_info(client, video_id=video_id)
            duration = isodate.parse_duration(video_info["duration"])
            length_text = timeformat.format_time(
                int(duration.total_seconds()), simple=True
            )
            return '{} -- \x02{}\x02: "{}" [YouTube: {}]'.format(
                url, title, content, length_text
            )
        except Exception:
            pass

    return f'{url} -- \x02{title}\x02: "{content}"'


@hook.command("gseis", "image")
def gse_gis(text):
    """<query> - Returns first image result using SearXNG."""

    result, error = searx_search(text, category="images")
    if error:
        return error

    image_url = result.get("url")
    content = result.get("content", "")
    title = result.get("title", "")

    if not image_url:
        return "No results found."

    # SearXNG does not reliably provide byte size / dimensions,
    # so we display best available info.
    return "{} -- \x02{}\x02: {}".format(
        image_url,
        formatting.truncate_str(title, 60),
        formatting.truncate_str(content, 120),
    )
