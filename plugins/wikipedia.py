"""Searches wikipedia and returns first sentence of article
Scaevolus 2009"""

import requests
from requests import RequestException
from yarl import URL

from cloudbot import hook
from cloudbot.util.web import get_session
from cloudbot.util import formatting

api_prefix = "http://en.wikipedia.org/w/api.php"
query_url = api_prefix + "?action=query&format=json"
search_url = query_url + "&list=search&redirect=1"
wp_api_url = URL("https://en.wikipedia.org/api/rest_v1/")

# Headers to avoid being blocked by Wikipedia
headers = {
    "User-Agent": "CloudBot/1.0 (https://github.com/CloudBotIRC/CloudBot; cloudbot@example.com)"
}


def make_summary_url(title) -> str:
    return str(wp_api_url / "page/summary" / title.replace(" ", "_"))


def get_info(title):
    with get_session().get(make_summary_url(title), headers=headers) as response:
        return response.json()


@hook.command("wiki", "wikipedia", "w")
def wiki(text, reply):
    """<phrase> - Gets first sentence of Wikipedia article on <phrase>."""

    search_params = {"srsearch": text.strip()}
    try:
        with get_session().get(
            search_url, params=search_params, headers=headers
        ) as response:
            response.raise_for_status()
            data = response.json()
    except RequestException:
        reply("Could not get Wikipedia page")
        raise

    for result in data["query"]["search"]:
        title = result["title"]
        info = get_info(title)
        if info["type"] != "standard":
            continue

        desc = info["extract"]
        url = info["content_urls"]["desktop"]["page"]

        break
    else:
        return "No results found."

    if desc:
        desc = formatting.truncate(desc, 200)
    else:
        desc = "(No Summary)"

    return f"{title} :: {desc} :: {url}"
