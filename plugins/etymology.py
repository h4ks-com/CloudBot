"""
Etymology plugin

Authors:
    - GhettoWizard
    - Scaevolus
    - linuxdaemon <linuxdaemon@snoonet.org>
"""

import re
from typing import Annotated

import ety
import requests
from bs4 import Tag
from requests import HTTPError

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting, web
from cloudbot.util.http import parse_soup
from cloudbot.util.web import get_session


@hook.command("etree")
def etymology_tree(word: Annotated[str, Arg("Word to look up")]):
    """retrieves etymolocial tree of <word>"""
    tree = ety.tree(word)
    if not tree:
        return [f"No etymology tree found for {word} :("]
    return tree.split("\n")


@hook.command("e", "etymology")
def etymology(
    event: CommandEvent, word: Annotated[str, Arg("Word to look up")]
):
    """retrieves the etymology of <word>"""

    url = "http://www.etymonline.com/index.php"

    response = get_session().get(url, params={"term": word})

    try:
        response.raise_for_status()
    except HTTPError as e:
        if e.response.status_code == 404:
            return f"No etymology found for {word} :("
        event.reply(f"Error reaching etymonline.com: {e.response.status_code}")
        raise

    if response.status_code != requests.codes.ok:
        return f"Error reaching etymonline.com: {response.status_code}"

    soup = parse_soup(response.text)

    prose_section = soup.find("section", class_=re.compile("prose"))

    if not isinstance(prose_section, Tag):
        return f"No etymology found for {word} :("

    paragraphs = prose_section.find_all("p")

    if not paragraphs:
        return f"No etymology found for {word} :("

    etym = " ".join(p.get_text() for p in paragraphs)

    etym = " ".join(etym.split())

    etym = formatting.truncate(etym, 200)

    etym += " Source: " + web.try_shorten(response.url)

    return etym
