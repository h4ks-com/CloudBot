import datetime
import re
from dataclasses import dataclass
from urllib.parse import quote

import requests

from cloudbot import hook
from cloudbot.util import colors
from cloudbot.util.formatting import truncate
from cloudbot.util.web import get_session

API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = (
    "Cloudbot/DEV (https://github.com/TotallyNotRobots/CloudBot; "
    "deaths plugin)"
)
DEFAULT_LIMIT = 5
MAX_LIMIT = 10
REQUEST_TIMEOUT = 15
MAX_NAME_LEN = 60
MAX_DESC_LEN = 200

_BOLD = colors.get_format("bold")
_CLEAR = colors.get_format("clear")
_GREY = colors.get_color("grey")
_CYAN = colors.get_color("cyan")

MONTHS = {
    name.lower(): i
    for i, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

ENTRY_RE = re.compile(r"^\*\s*\[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*,?\s*(.*)$")


@dataclass
class Death:
    name: str
    death_date: str
    link: str
    details: str


def _api_get(params: dict) -> dict:
    response = get_session(timeout=REQUEST_TIMEOUT).get(
        API_URL,
        params={**params, "format": "json"},
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()


def _fetch_sections(year: int) -> list[dict]:
    data = _api_get(
        {
            "action": "parse",
            "page": f"Deaths in {year}",
            "prop": "sections",
        }
    )
    return data["parse"]["sections"]


def _fetch_section_wikitext(year: int, section_index: str) -> str:
    data = _api_get(
        {
            "action": "parse",
            "page": f"Deaths in {year}",
            "prop": "wikitext",
            "section": section_index,
        }
    )
    return data["parse"]["wikitext"]["*"]


def _clean_wikitext(text: str) -> str:
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"'''(.+?)'''", r"\1", text)
    text = re.sub(r"''(.+?)''", r"\1", text)
    text = re.sub(r"\[\[[^\]]*\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


def _parse_entries(
    wikitext: str, year: int, month: int, day: int, limit: int
) -> list[Death]:
    date_str = datetime.date(year, month, day).isoformat()
    entries: list[Death] = []
    for raw_line in wikitext.splitlines():
        match = ENTRY_RE.match(raw_line.strip())
        if not match:
            continue
        article_name = match.group(1).strip()
        details = _clean_wikitext(match.group(2))
        link = "https://en.wikipedia.org/wiki/" + quote(
            article_name.replace(" ", "_")
        )
        entries.append(
            Death(
                name=article_name,
                death_date=date_str,
                link=link,
                details=details,
            )
        )
        if len(entries) >= limit:
            break
    return entries


def _find_recent(today: datetime.date, limit: int) -> list[Death]:
    for year in (today.year, today.year - 1):
        sections = _fetch_sections(year)
        current_month: int | None = None
        for section in sections:
            line = section["line"]
            if not line.isdigit():
                current_month = MONTHS.get(line.lower())
                continue
            if current_month is None:
                continue
            day = int(line)
            try:
                section_date = datetime.date(year, current_month, day)
            except ValueError:
                continue
            if section_date > today:
                continue
            wikitext = _fetch_section_wikitext(year, section["index"])
            entries = _parse_entries(wikitext, year, current_month, day, limit)
            if entries:
                return entries
    return []


def _parse_count(text: str) -> int | str:
    stripped = text.strip()
    if not stripped:
        return DEFAULT_LIMIT
    try:
        value = int(stripped)
    except ValueError:
        return f"Invalid count '{stripped}'. Please provide a number."
    return max(1, min(value, MAX_LIMIT))


def format_deaths(deaths: list[Death]) -> list[str]:
    lines = []
    for d in deaths:
        name = truncate(d.name, MAX_NAME_LEN)
        details = (
            truncate(d.details.rstrip("."), MAX_DESC_LEN) if d.details else ""
        )
        line = f"{_BOLD}{name}{_CLEAR} " f"{_GREY}{d.death_date}{_CLEAR}"
        if details:
            line += f" - {details}"
        line += f" :: {_CYAN}{d.link}{_CLEAR}"
        lines.append(line)
    return lines


@hook.command("obituary", "obituaries", autohelp=False)
def deaths(text: str) -> str | list[str]:
    """[count] - Show up to <count> (default 5, max 10) most recent notable deaths."""
    count = _parse_count(text)
    if isinstance(count, str):
        return count

    try:
        results = _find_recent(datetime.date.today(), count)
    except requests.exceptions.RequestException as err:
        return f"Failed to fetch deaths: {err}"

    if not results:
        return "No recent deaths found."

    return format_deaths(results)
