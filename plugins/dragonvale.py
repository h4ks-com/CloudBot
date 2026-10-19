import re
from typing import Annotated

from requests import HTTPError

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util.http import parse_soup
from cloudbot.util.timeparse import time_parse
from cloudbot.util.web import get_session

search_url = "http://dragonvale.wikia.com/api/v1/Search/list"

egg_calc_url = "http://www.dragonvalebreedingguide.com/dragonvale-calculator"


def striphtml(data):
    string = re.compile(r"<.*?>")
    return string.sub("", data)


@hook.command("dragon", "ds")
def dragonsearch(
    event: CommandEvent, query: Annotated[str, Arg("Text to search for")]
):
    """Searches the dragonvale wiki for the specified text."""
    params = {"query": query, "limit": 1}

    r = get_session().get(search_url, params=params)

    try:
        r.raise_for_status()
    except HTTPError:
        event.reply(f"The API returned error code {r.status_code}.")
        raise

    if not r.status_code == 200:
        return f"The API returned error code {r.status_code}."

    data = r.json()["items"][0]
    out = "\x02{}\x02 -- {}: {}".format(
        data["title"],
        striphtml(data["snippet"]).split("&hellip;")[0].strip(),
        data["url"],
    )
    return out


@hook.command("eggcalc", "dragoncalc", "dc")
def egg_calculator(
    time_text: Annotated[str, Arg("Incubation time, like 5 hours, 30 minutes")],
):
    """Parses dragonvalebreedingguide.com for a list of possible dragons based on the incubation time.
    Enter the time as 5 hours, 30 minutes. For upgraded incubation times put 'upgrade' at the front of the time length
    """
    params: dict[str, int | float] = {"avail": 1}
    if time_text.lower().startswith("upgrade"):
        timer = time_text.replace("upgrade", "")
        time2 = time_parse(timer.strip())
        if not time2:
            return "invalid time format"

        params["time2"] = time2
    else:
        timer = time_text
        time = time_parse(timer.strip())
        if not time:
            return "invalid time format"

        params["time"] = time

    r = get_session().get(egg_calc_url, params=params, timeout=5)
    soup = parse_soup(r.text)
    dragons = []
    for line in soup.find_all(
        "td", attrs={"class": "views-field views-field-title"}
    ):
        dragons.append(line.text.replace("\n", "").strip())

    return ", ".join(dragons)
