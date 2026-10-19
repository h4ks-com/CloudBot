import re
import urllib.parse
from typing import Annotated

import requests
from requests import HTTPError

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting, web
from cloudbot.util.http import parse_xml
from cloudbot.util.web import get_session

api_url = "http://api.wolframalpha.com/v2/query"
query_url = "http://www.wolframalpha.com/input/?i={}"


@hook.command("wolframalpha", "wa", "calc", "ca", "math", "convert")
def wolframalpha(
    bot: CloudBot,
    event: CommandEvent,
    query: Annotated[str, Arg("What to compute")],
):
    """Computes <query> using Wolfram Alpha."""
    api_key = bot.config.get_api_key("wolframalpha")
    if not api_key:
        return "error: missing api key"

    params = {"input": query, "appid": api_key}
    request = get_session().get(api_url, params=params)

    try:
        request.raise_for_status()
    except HTTPError as e:
        event.reply(f"Error getting query: {e.response.status_code}")
        raise

    if request.status_code != requests.codes.ok:
        return f"Error getting query: {request.status_code}"

    result = parse_xml(request.content)

    # get the URL for a user to view this query in a browser
    short_url = web.try_shorten(
        query_url.format(urllib.parse.quote_plus(query))
    )

    pod_texts = []
    for pod in result.xpath("//pod[@primary='true']"):
        title = pod.attrib["title"]
        if pod.attrib["id"] == "Input":
            continue

        results = []
        for subpod in pod.xpath("subpod/plaintext/text()"):
            subpod = subpod.strip().replace("\\n", "; ")
            subpod = re.sub(r"\s+", " ", subpod)
            if subpod:
                results.append(subpod)
        if results:
            pod_texts.append(title + ": " + ", ".join(results))

    ret = " - ".join(pod_texts)

    if not pod_texts:
        return "No results."

    # I have no idea what this regex does.
    ret = re.sub(r"\\(.)", r"\1", ret)
    ret = formatting.truncate(ret, 250)

    if not ret:
        return "No results."

    return f"{ret} - {short_url}"
