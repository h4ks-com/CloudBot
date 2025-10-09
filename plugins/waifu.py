# Should make comamnd for every free API out there they say...

import requests

from cloudbot import hook
from cloudbot.util.web import get_session

API_URL = "https://api.waifu.im/"
tags = []


def refresh_tags():
    global tags
    tags = []
    response = get_session().get(API_URL + "tags")
    if response.status_code == 200:
        obj = response.json()
        tags.extend(obj["versatile"])
        tags.extend(obj["nsfw"])


@hook.on_start()
def setup(bot):
    refresh_tags()


@hook.command("waifu", autohelp=False)
def waifu(text):
    """<waifu> - returns a random waifu image with <tag> using waifu.im. Use 'tags' to get available tags. Use 'refresh' to refresh tags."""
    global tags
    if text == "tags":
        return "Available tags: " + ", ".join(tags)
    if text == "refresh":
        refresh_tags()
        return "Available tags: " + ", ".join(tags)

    request_tags = list(text.split())
    params = {
        "included_tags": request_tags,
    }

    response = get_session().get(API_URL + "search", params=params)

    if response.status_code == 200:
        data = response.json()
        image = data["images"][0]
        disclaimer = "\x02NSFW!\x02 - " if image["is_nsfw"] else ""
        dimensions = f"{image['width']}x{image['height']}"
        return f"{disclaimer}{dimensions} - \x02tags\x02: {', '.join([t['name'] for t in image['tags']])} - {image['url']}"
    else:
        return "Error fetching waifu image." + " -- " + response.text
