# Search games on psn
# Author: Matheus Fillipe
# Date: 24/09/2022

from dataclasses import dataclass
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from cloudbot import hook
from cloudbot.util import queue
from cloudbot.util.web import get_session

BASE_URL = "https://store.playstation.com"
LANG = "en-us"
SEARCH_URL = BASE_URL + "/{}/search/{}"
GAME_URL = BASE_URL + "/{}/product/{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:103.0) Gecko/20100101 Firefox/103.0",
}

LANGS = {
    "en-us",
    "pt-br",
    "es-es",
    "fr-fr",
    "de-de",
    "it-it",
    "ja-jp",
    "ko-kr",
    "ru-ru",
    "zh-cn",
    "zh-hk",
    "zh-tw",
}


results_queue = queue.Queue()


@dataclass
class Game:
    name: str
    price: str
    url: str
    description: str

    def __str__(self):
        return f"{self.name} - {self.price} - {self.description} - {self.url}"


def search_game(query: str, lang: str) -> List[Game]:
    url = SEARCH_URL.format(lang, quote(query))
    r = get_session().get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    grid = soup.find("ul", class_="psw-grid-list psw-l-grid")
    if not grid:
        return []
    games = []
    i = 0
    for game in grid.find_all("li") or []:
        name_elem = game.find(
            "span", {"data-qa": f"search#productTile{i}#product-name"}
        )
        price_elem = game.find(
            "span", {"data-qa": f"search#productTile{i}#price#display-price"}
        )
        if not name_elem or not price_elem:
            i += 1
            continue

        name = name_elem.text.strip()
        price = price_elem.text.strip()

        desc_elem = game.find(
            "span",
            {"data-qa": f"search#productTile{i}#service-upsell#descriptorText"},
        )
        description = desc_elem.text.strip() if desc_elem else ""

        ptype_elem = game.find(
            "span", {"data-qa": f"search#productTile{i}#product-type"}
        )
        if ptype_elem:
            description = ptype_elem.text.strip() + " " + description

        link = game.find("a")
        href = link.get("href", "") if link else ""
        url = BASE_URL + (href if isinstance(href, str) else "")
        games.append(Game(name, price, url, description.strip()))
        i += 1
    return games


@hook.command("psnn", autohelp=False)
def psnn(text: str, message: str, chan, nick):
    """Next result in the queue for playstation games"""
    global results_queue
    results = results_queue[chan][nick]
    if len(results) == 0:
        return "No [more] results found."

    return str(results.pop())


@hook.command("psn", "playstation", autohelp=False)
def psn(text, message, chan, nick, reply):
    """[lang] <game> - Search for a game on psn"""
    global results_queue
    if not text:
        return "Please provide a game to search for."

    lang = LANG
    query = text
    if text.split()[0] in LANGS:
        lang = text.split()[0]
        query = " ".join(text.split()[1:])

    if not query:
        return "Please provide a game to search for."

    games = search_game(query, lang)
    if not games:
        return "No results found."

    results_queue[chan][nick] = games
    return psnn(text, message, chan, nick)
