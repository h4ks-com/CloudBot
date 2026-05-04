import json
import re
from urllib.parse import quote

from requests import HTTPError, RequestException

from cloudbot import hook
from cloudbot.util import web
from cloudbot.util.queue import Queue
from cloudbot.util.web import get_session

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

# CDN suggestion API — not bot-protected, returns JSONP
SUGGEST_URL = "https://sg.media-imdb.com/suggests/{prefix}/{query}.json"
TITLE_URL = "https://www.imdb.com/title/{tt_id}/"
GRAPHQL_URL = "https://caching.graphql.imdb.com/"

# Internal IMDB GraphQL headers — no API key required
GRAPHQL_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
    "x-imdb-client-name": "imdb-web-next",
    "x-imdb-user-language": "en-US",
    "x-imdb-user-country": "US",
}

GRAPHQL_QUERY = """
query GetTitle($id: ID!) {
    title(id: $id) {
        titleText { text }
        releaseYear { year }
        titleType { id }
        runtime { seconds }
        ratingsSummary { aggregateRating voteCount }
        genres { genres { text } }
    }
}
"""

results_queue = Queue()


def search_imdb(query: str) -> list[str] | None:
    """Search IMDB via the suggestion CDN, returns list of tt_ids."""
    query_norm = query.lower().replace(" ", "_")
    prefix = query_norm[0] if query_norm and query_norm[0].isalnum() else "a"
    url = SUGGEST_URL.format(prefix=prefix, query=quote(query_norm, safe="_"))

    try:
        response = get_session().get(url, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

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
    """Fetch title info via IMDB's internal GraphQL API (no key needed)."""
    try:
        response = get_session().post(
            GRAPHQL_URL,
            headers=GRAPHQL_HEADERS,
            json={"query": GRAPHQL_QUERY, "variables": {"id": tt_id}},
            timeout=8,
        )
        response.raise_for_status()
    except RequestException:
        return None

    data = response.json()
    title_data = data.get("data", {}).get("title")
    if not title_data:
        return None

    rating_summary = title_data.get("ratingsSummary") or {}
    rating = rating_summary.get("aggregateRating")
    runtime_seconds = (title_data.get("runtime") or {}).get("seconds")
    genres = [
        g["text"] for g in (title_data.get("genres") or {}).get("genres", [])
    ]

    return {
        "title": (title_data.get("titleText") or {}).get("text", "Unknown"),
        "year": str((title_data.get("releaseYear") or {}).get("year", "")),
        "type": (title_data.get("titleType") or {}).get("id", ""),
        "runtime": f"{runtime_seconds // 60} min" if runtime_seconds else "",
        "genre": ", ".join(genres),
        "rating": str(rating) if rating else "N/A",
        "url": TITLE_URL.format(tt_id=tt_id),
    }


def _format_result(info: dict[str, str]) -> str:
    short_url = web.try_shorten(info["url"])

    title_year = f"\x02{info['title']}"
    if info["year"]:
        title_year += f" ({info['year']})"
    title_year += "\x02"

    parts = [title_year]

    rating = info["rating"]
    parts.append(f"IMDB: {rating}/10" if rating != "N/A" else "IMDB: N/A")

    if info.get("runtime"):
        parts.append(info["runtime"])

    if info.get("genre"):
        parts.append(info["genre"])

    parts.append(short_url)
    return " - ".join(parts)


@hook.command("imdbn", "imdb_next", autohelp=False)
def imdbn(nick, chan):
    """Get next IMDB result from your search results."""
    results = results_queue[chan][nick]
    if len(results) == 0:
        return "No [more] results found."

    tt_id = results.pop()
    info = get_imdb_info(tt_id)
    if not info:
        return "Error retrieving movie information"

    return _format_result(info)


@hook.command("imdb")
def imdb(text: str, nick, chan) -> str:
    """<query> - Search IMDB for movie/show information including ratings"""
    if not text.strip():
        return "Please provide a movie or show title to search for."

    tt_ids = search_imdb(text.strip())
    if not tt_ids:
        return f"No IMDB results found for '{text.strip()}'"

    results_queue[chan][nick] = tt_ids[1:]

    info = get_imdb_info(tt_ids[0])
    if not info:
        return "Error retrieving movie information"

    return _format_result(info)
