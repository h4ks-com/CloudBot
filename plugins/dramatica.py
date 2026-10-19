import re
from typing import Annotated
from urllib import parse

import requests
from lxml import html
from requests import HTTPError

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting
from cloudbot.util.web import get_session

api_url = "http://encyclopediadramatica.se/api.php"
ed_url = "http://encyclopediadramatica.se/"


@hook.command()
def drama(
    phrase: Annotated[str, Arg("Article to look up")], event: CommandEvent
):
    """gets the first paragraph of the Encyclopedia Dramatica article on <phrase>"""

    search_response = get_session().get(
        api_url, params={"action": "opensearch", "search": phrase}
    )

    try:
        search_response.raise_for_status()
    except HTTPError:
        event.reply(f"Error searching: {search_response.status_code}")
        raise

    if search_response.status_code != requests.codes.ok:
        return f"Error searching: {search_response.status_code}"

    data = search_response.json()

    if not data[1]:
        return "No results found."
    article_name = data[1][0].replace(" ", "_")

    url = ed_url + parse.quote(article_name, "")

    page_response = get_session().get(url)

    try:
        page_response.raise_for_status()
    except HTTPError:
        event.reply(f"Error getting page: {page_response.status_code}")
        raise

    if page_response.status_code != requests.codes.ok:
        return f"Error getting page: {page_response.status_code}"

    page = html.fromstring(page_response.text)

    for p in page.xpath('//div[@id="bodyContent"]/p'):
        if p.text_content():
            summary = " ".join(p.text_content().splitlines())
            summary = re.sub(r"\[\d+\]", "", summary)
            summary = formatting.truncate(summary, 220)
            return f"{summary} - {url}"

    return "Unknown Error."
