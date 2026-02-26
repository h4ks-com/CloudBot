"""
Etymology plugin

Authors:
    - GhettoWizard
    - Scaevolus
    - linuxdaemon <linuxdaemon@snoonet.org>
"""

import re

import ety
import requests
from requests import HTTPError

from cloudbot import hook
from cloudbot.util import formatting, web
from cloudbot.util.http import parse_soup
from cloudbot.util.web import get_session


@hook.command("etree")
def etymology_tree(nick, text, reply):
    """<word> - retrieves etymolocial tree of <word>"""
    # pager = CommandPager.from_multiline_string(str(ety.tree(text.strip())))
    for page in str(ety.tree(text.strip())).split("\n"):
        reply(page)


@hook.command("e", "etymology")
def etymology(text, reply):
    """<word> - retrieves the etymology of <word>"""

    url = "http://www.etymonline.com/index.php"

    response = get_session().get(url, params={"term": text})

    try:
        response.raise_for_status()
    except HTTPError as e:
        if e.response.status_code == 404:
            return f"No etymology found for {text} :("
        reply(f"Error reaching etymonline.com: {e.response.status_code}")
        raise

    if response.status_code != requests.codes.ok:
        return f"Error reaching etymonline.com: {response.status_code}"

    soup = parse_soup(response.text)

    prose_section = soup.find("section", class_=re.compile("prose"))

    if not prose_section:
        return f"No etymology found for {text} :("

    paragraphs = prose_section.find_all("p")

    if not paragraphs:
        return f"No etymology found for {text} :("

    etym = " ".join(p.get_text() for p in paragraphs)

    etym = " ".join(etym.split())

    etym = formatting.truncate(etym, 200)

    etym += " Source: " + web.try_shorten(response.url)

    return etym
