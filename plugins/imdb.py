import json
import re
from urllib.parse import quote

from bs4 import BeautifulSoup
from requests import HTTPError

from cloudbot import hook
from cloudbot.util import web
from cloudbot.util.queue import Queue
from cloudbot.util.web import get_session

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

# Suggestion API: returns JSONP with title/year/id data without bot protection
SUGGEST_URL = "https://sg.media-imdb.com/suggests/{prefix}/{query}.json"
RATINGS_URL = "https://www.imdb.com/title/{tt_id}/ratings/"
TITLE_URL = "https://www.imdb.com/title/{tt_id}/"

results_queue = Queue()


def search_imdb(query: str) -> list[str] | None:
    """Search IMDB for movies/shows matching the query, returns list of tt_ids."""
    query_norm = query.lower().replace(" ", "_")
    prefix = query_norm[0] if query_norm and query_norm[0].isalnum() else "a"
    url = SUGGEST_URL.format(prefix=prefix, query=quote(query_norm, safe="_"))

    try:
        response = get_session().get(url, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

    # Response is JSONP: imdb$query({...})
    json_match = re.search(r"\((\{.*\})\)", response.text, re.DOTALL)
    if not json_match:
        return None

    data = json.loads(json_match.group(1))
    tt_ids = [
        item["id"]
        for item in data.get("d", [])
        if item.get("id", "").startswith("tt")
    ]
    return tt_ids or None


def get_imdb_info(tt_id: str) -> dict[str, str] | None:
    """Extract movie information from IMDB ratings page."""
    url = RATINGS_URL.format(tt_id=tt_id)
    try:
        response = get_session().get(url, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

    soup = BeautifulSoup(response.content, "html.parser")
    next_data_elem = soup.select_one("script#__NEXT_DATA__")
    if not next_data_elem:
        return None

    try:
        next_data = json.loads(next_data_elem.string or "")
        entity = next_data["props"]["pageProps"]["contentData"]["entityMetadata"]
    except (json.JSONDecodeError, KeyError, ValueError):
        return None

    # originalTitleText has the full title (e.g. "The Matrix" vs "Matrix")
    title_data = entity.get("originalTitleText") or entity.get("titleText") or {}
    title = title_data.get("text", "Unknown Title")

    release_year = entity.get("releaseYear") or {}
    year = str(release_year["year"]) if release_year.get("year") else ""

    ratings = entity.get("ratingsSummary") or {}
    rating = ratings.get("aggregateRating")
    user_score = str(rating) if rating is not None else "N/A"

    return {
        "title": title,
        "year": year,
        "user_score": user_score,
        "url": TITLE_URL.format(tt_id=tt_id),
    }


def _format_result(info: dict[str, str]) -> str:
    short_url = web.try_shorten(info["url"])

    title_year = info["title"]
    if info["year"]:
        title_year += f" ({info['year']})"

    user_score_text = (
        f"User: {info['user_score']}/10"
        if info["user_score"] != "N/A"
        else "User: N/A"
    )

    return f"\x02{title_year}\x02 - {user_score_text} - {short_url}"


@hook.command("imdbn", "imdb_next", autohelp=False)
def imdbn(nick, chan, text):
    """Get next IMDB result from your search results."""
    results = results_queue[chan][nick]
    if len(results) == 0:
        return "No [more] results found."

    tt_id = results.pop()
    info = get_imdb_info(tt_id)
    if not info:
        return "Error retrieving movie information from IMDB"

    return _format_result(info)


@hook.command("imdb")
def imdb(text: str, nick, chan) -> str:
    """<query> - Search IMDB for movie/show information including ratings"""
    if not text.strip():
        return "Please provide a movie or show title to search for."

    query = text.strip()

    tt_ids = search_imdb(query)
    if not tt_ids:
        return f"No IMDB results found for '{query}'"

    results_queue[chan][nick] = tt_ids[1:]

    info = get_imdb_info(tt_ids[0])
    if not info:
        return "Error retrieving movie information from IMDB"

    return _format_result(info)
