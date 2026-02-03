from functools import lru_cache
from urllib.parse import quote

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.queue import Queue
from curl_cffi import requests

SEARCH_URL = "https://www.justice.gov/multimedia-search"
HEADERS = {
    "accept": "*/*",
    "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    "sec-ch-ua-mobile": "?0",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
}

MAX_RESULTS = 20


@lru_cache
def get_queue():
    return Queue()


def search_files(query: str) -> dict:
    """Search the Epstein files via justice.gov API"""
    params = {"keys": query, "page": "1"}
    response = requests.get(SEARCH_URL, headers=HEADERS, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def format_result(hit_data: dict) -> str:
    """Format a single search result for IRC display"""
    file_name = hit_data["ORIGIN_FILE_NAME"]
    file_url = quote(hit_data["ORIGIN_FILE_URI"], safe=':/')
    return f"\x02{file_name}\x02 :: {file_url}"


@hook.command("epstein", autohelp=False)
def epstein_search(text: str, bot, chan: str, nick: str) -> str:
    """<query> - Searches the Epstein files for occurrences of the query"""
    query = text.strip()
    if not query:
        return "Please provide a search query."

    try:
        data = search_files(query)
        total = data["hits"]["total"]["value"]

        if total == 0:
            return f"No results found for '{query}' in the Epstein files."

        hits = data["hits"]["hits"][:MAX_RESULTS]
        queue = get_queue()
        # Reverse so pop() gives us FIFO behavior
        queue[chan][nick] = [hit["_source"] for hit in hits][::-1]

        count_text = formatting.pluralize_auto(total, "occurrence")
        first_result = format_result(queue[chan][nick].pop())

        remaining = len(queue[chan][nick])
        if remaining > 0:
            return f"Found {count_text} of '{query}' :: {first_result} :: ({remaining} more, use .epsteinn)"
        else:
            return f"Found {count_text} of '{query}' :: {first_result}"

    except requests.exceptions.RequestException as e:
        return f"Error searching: {e}"
    except (KeyError, IndexError) as e:
        return f"Error parsing results: {e}"


@hook.command("epstein_next", "epsteinn", autohelp=False)
def epstein_next(text: str, chan: str, nick: str) -> str:
    """[nick] - Gets the next result from the last Epstein search"""
    target_nick = text.strip() or nick

    queue = get_queue()
    try:
        results = queue[chan][target_nick]
    except KeyError:
        return f"No results found for {target_nick}. Try .epstein <query> first."

    if len(results) == 0:
        return f"No more results for {target_nick}."

    next_result = results.pop()
    formatted = format_result(next_result)

    remaining = len(results)
    if remaining > 0:
        return f"{formatted} :: ({remaining} more remaining)"
    else:
        return formatted
