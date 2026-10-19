# Search games on psn
# Author: Matheus Fillipe
# Date: 24/09/2022

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
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


def search_game(query: str, lang: str) -> list[Game]:
    url = SEARCH_URL.format(lang, quote(query))
    r = get_session().get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    grid = soup.find("ul", class_="psw-grid-list psw-l-grid")
    if not isinstance(grid, Tag):
        return []
    games = []
    i = 0
    for game in grid.find_all("li") or []:
        if not isinstance(game, Tag):
            i += 1
            continue
        name_elem = game.find(
            "span",
            attrs={"data-qa": f"search#productTile{i}#product-name"},
        )
        price_elem = game.find(
            "span",
            attrs={"data-qa": f"search#productTile{i}#price#display-price"},
        )
        if not isinstance(name_elem, Tag) or not isinstance(price_elem, Tag):
            i += 1
            continue

        name = name_elem.text.strip()
        price = price_elem.text.strip()

        desc_elem = game.find(
            "span",
            attrs={
                "data-qa": f"search#productTile{i}#service-upsell#descriptorText"
            },
        )
        description = (
            desc_elem.text.strip() if isinstance(desc_elem, Tag) else ""
        )

        ptype_elem = game.find(
            "span",
            attrs={"data-qa": f"search#productTile{i}#product-type"},
        )
        if isinstance(ptype_elem, Tag):
            description = ptype_elem.text.strip() + " " + description

        link = game.find("a")
        href = link.get("href", "") if isinstance(link, Tag) else ""
        url = BASE_URL + (href if isinstance(href, str) else "")
        games.append(Game(name, price, url, description.strip()))
        i += 1
    return games


@hook.command("psnn", autohelp=False)
def psnn(text: str, event: CommandEvent, chan: str, nick: str):
    """- Show the next playstation game result"""
    results = results_queue[chan][nick]
    if len(results) == 0:
        return "No [more] results found."

    return str(results.pop())


@hook.command("psn", "playstation", autohelp=False)
def psn(
    event: CommandEvent,
    chan: str,
    nick: str,
    *,
    lang: Annotated[
        str | None, Arg("Store language", choices=tuple(sorted(LANGS)))
    ] = None,
    query: Annotated[str, Arg("Game to search for")],
):
    """Search for a game on psn"""
    lang = lang or LANG

    games = search_game(query, lang)
    if not games:
        return "No results found."

    results_queue[chan][nick] = games
    return psnn("", event, chan, nick)
