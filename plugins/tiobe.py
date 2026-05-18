# Returns the tiobe index ranking for the month
# Author: Matheus Fillipe
# Date: 19/09/2022

import re
from dataclasses import dataclass
from enum import Enum, auto

from bs4 import BeautifulSoup, Tag

from cloudbot import hook
from cloudbot.util.web import get_session

BASE_URL = "https://www.tiobe.com"
TABLE_URL = f"{BASE_URL}/tiobe-index/"


def get_float(s: str) -> float:
    """Returns first float number contained in a string."""
    match = re.search(r"\d+\.\d+", s)
    if match is None:
        return 0.0
    return float(match[0])


class ChangeDirection(Enum):
    UP = auto()
    DOWN = auto()
    NONE = auto()


@dataclass
class TiobeRow:
    rank: int
    last_month_rank: int
    change_direction: ChangeDirection
    logo_url: str
    language: str
    rating: float
    change: float

    def __str__(self):
        return (
            f"{self.rank}) {self.language} ({self.rating:.2f}) "
            f"{'▲' if self.change_direction == ChangeDirection.UP else '▼' if self.change_direction == ChangeDirection.DOWN else ''} {self.change:.2f}%"
        )


@dataclass
class TiobeRowBuilder:
    rank: Tag
    last_month_rank: Tag
    change_direction: Tag
    logo_url: Tag
    language: Tag
    rating: Tag
    change: Tag

    def build(self) -> TiobeRow:
        rank = int(self.rank.text.strip())
        last_month_rank = int(self.last_month_rank.text.strip())

        direction_img = self.change_direction.find("img")
        change_direction = ChangeDirection.NONE
        if isinstance(direction_img, Tag):
            src = direction_img.get("src", "")
            src_str = src if isinstance(src, str) else ""
            if "up.png" in src_str:
                change_direction = ChangeDirection.UP
            elif "down.png" in src_str:
                change_direction = ChangeDirection.DOWN

        logo_img = self.logo_url.find("img")
        logo_url = ""
        if isinstance(logo_img, Tag):
            src = logo_img.get("src", "")
            logo_url = BASE_URL + src if isinstance(src, str) else ""

        return TiobeRow(
            rank=rank,
            last_month_rank=last_month_rank,
            change_direction=change_direction,
            logo_url=logo_url,
            language=self.language.text.strip(),
            rating=get_float(self.rating.text),
            change=get_float(self.change.text),
        )


def get_table() -> list[TiobeRow]:
    """Returns the tiobe index table."""
    r = get_session().get(TABLE_URL)
    soup = BeautifulSoup(r.content, "html.parser")
    table = soup.find(
        "table", attrs={"class": "table table-striped table-top20"}
    )
    table2 = soup.find("table", attrs={"id": "otherPL"})
    if not isinstance(table, Tag) or not isinstance(table2, Tag):
        return []
    tbody1 = table.find("tbody")
    tbody2 = table2.find("tbody")
    if not isinstance(tbody1, Tag) or not isinstance(tbody2, Tag):
        return []
    top20 = [
        TiobeRowBuilder(
            *(ele for ele in row.find_all("td") if isinstance(ele, Tag))
        ).build()
        for row in tbody1.find_all("tr")
        if isinstance(row, Tag)
    ]
    elms = [
        row.find_all("td")
        for row in tbody2.find_all("tr")
        if isinstance(row, Tag)
    ]
    others = []
    for rank, language, rating in elms:
        others.append(
            TiobeRow(
                rank=int(rank.text.strip()),
                last_month_rank=0,
                change_direction=ChangeDirection.NONE,
                logo_url="",
                language=language.text.strip(),
                rating=get_float(rating.text),
                change=0.0,
            )
        )
    return top20 + others


@hook.command("tiobe", "tiobeindex", autohelp=False)
def tiobe(reply, text):
    """Returns the tiobe index ranking for the month."""
    rows = get_table()
    arg = ""
    if text.split():
        arg = text.split()[0]
    # If is digit return the ranking for the language
    if arg.isdigit():
        try:
            for row in rows[int(arg) - 1 : int(arg) + 5]:
                reply(str(row))
        except IndexError:
            reply("Invalid rank position")

    # If is a language return the ranking for the language
    elif arg:
        for row in rows:
            if arg.casefold() in row.language.casefold():
                reply(str(row))
                return
        reply("Language not found in the top 50")
        return

    else:
        for row in rows[:5]:
            reply(str(row))
