# https://howlongtobeat.com - How Long To Beat games
# Author: Matheus Fillipe
# Date: 29/09/2022

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

from cloudbot import hook
from cloudbot.util.queue import Queue
from cloudbot.util.web import get_session

BASE_URL = "https://howlongtobeat.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}


@dataclass
class Game:
    name: str
    url: str
    main_story: str
    main_extras: str
    completionist: str

    def __str__(self):
        return f"{self.name} - {self.url} - Main: {self.main_story} - Main+Extra: {self.main_extras} - Completionist: {self.completionist}"


results_queue = Queue()


def _format_hours(seconds) -> str:
    if not seconds:
        return "N/A"
    hours = float(seconds) / 3600
    return (
        f"{int(hours)} Hours" if hours == int(hours) else f"{hours:.1f} Hours"
    )


def _get_token() -> str | None:
    """Fetch a short-lived auth token required by the /api/finder endpoint."""
    try:
        r = get_session().get(
            f"{BASE_URL}/api/finder/init",
            params={"t": int(time.time() * 1000)},
            headers=HEADERS,
            timeout=8,
        )
        if r.ok:
            return r.json().get("token")
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


def _parse_items(items: list) -> list[Game]:
    games = []
    for item in items[:5]:
        try:
            games.append(
                Game(
                    name=item.get("game_name", "Unknown"),
                    url=f"{BASE_URL}/game/{item.get('game_id', '')}",
                    main_story=_format_hours(item.get("comp_main")),
                    main_extras=_format_hours(item.get("comp_plus")),
                    completionist=_format_hours(item.get("comp_100")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return games


def try_api_search(game_name: str) -> list[Game] | None:
    """Search via the official HLTB API (requires token from /api/finder/init)."""
    token = _get_token()
    if not token:
        return None

    payload = {
        "searchType": "games",
        "searchTerms": game_name.split(),
        "searchPage": 1,
        "size": 20,
        "searchOptions": {
            "games": {
                "userId": 0,
                "platform": "",
                "sortCategory": "popular",
                "rangeCategory": "main",
                "rangeTime": {"min": 0, "max": 0},
                "gameplay": {
                    "perspective": "",
                    "flow": "",
                    "genre": "",
                    "difficulty": "",
                },
                "rangeYear": {"min": "", "max": ""},
                "modifier": "",
            },
            "users": {"sortCategory": "postcount"},
            "lists": {"sortCategory": "follows"},
            "filter": "",
            "sort": 0,
            "randomizer": 0,
        },
        "useCache": True,
    }

    try:
        r = get_session().post(
            f"{BASE_URL}/api/finder",
            headers={
                **HEADERS,
                "Content-Type": "application/json",
                "x-auth-token": token,
            },
            json=payload,
            timeout=10,
        )
        if r.ok and r.content:
            data = r.json()
            items = data.get("data", [])
            if items:
                return _parse_items(items)
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


def _extract_from_next_data(html: str) -> Game | None:
    """Extract game data from __NEXT_DATA__ JSON embedded in the page."""
    try:
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
            html,
            re.DOTALL,
        )
        if not m:
            return None
        data = json.loads(m.group(1))
        items = (
            data.get("props", {})
            .get("pageProps", {})
            .get("game", {})
            .get("data", {})
            .get("game", [])
        )
        if not items:
            return None
        g = items[0]
        return Game(
            name=g.get("game_name", "Unknown"),
            url=f"{BASE_URL}/game/{g.get('game_id', '')}",
            main_story=_format_hours(g.get("comp_main")),
            main_extras=_format_hours(g.get("comp_plus")),
            completionist=_format_hours(g.get("comp_100")),
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


def scrape_hltb_search(game_name: str) -> list[Game] | None:
    """Fallback: find HLTB game IDs via DuckDuckGo, then scrape each game page."""
    search_query = f"{game_name} site:howlongtobeat.com/game"
    search_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; bot)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    game_ids: list[str] = []
    for search_url in [
        f"https://duckduckgo.com/lite/?q={quote(search_query)}",
        f"https://www.startpage.com/sp/search?query={quote(search_query)}",
    ]:
        try:
            r = get_session().get(search_url, headers=search_headers, timeout=5)
            if r.ok:
                ids = list(
                    dict.fromkeys(
                        re.findall(r"howlongtobeat\.com/game/(\d+)", r.text)
                    )
                )[:5]
                if ids:
                    game_ids = ids
                    break
        except (requests.RequestException, requests.Timeout):
            continue

    if not game_ids:
        return None

    games = []
    for game_id in game_ids:
        try:
            game_url = f"{BASE_URL}/game/{game_id}"
            r = get_session().get(game_url, headers=HEADERS, timeout=8)
            if not r.ok:
                continue
            game = _extract_from_next_data(r.text)
            if game:
                games.append(game)
        except (requests.RequestException, requests.Timeout):
            continue

    return games or None


@hook.command("hltbn", "hltb_next", autohelp=False)
def hltbn(text, nick, chan):
    """Displays next game in queue for nick."""
    if text:
        nick = text.strip().split()[0]
        if nick not in results_queue[chan]:
            return f"{nick} has no hltb game in queue."

    if len(results_queue[chan][nick]) == 0:
        return "No [more] results for you"

    game: Game = results_queue[chan][nick].pop()
    return str(game)


@hook.command("howlongtobeat", "hltb", autohelp=False)
def howlongtobeat(text, nick, chan):
    """<game> - Search for a game on How Long To Beat"""
    if not text:
        return "Please provide a game name to search for"

    games = try_api_search(text) or scrape_hltb_search(text)
    if games:
        results_queue[chan][nick] = games[1:]
        return str(games[0])

    encoded = quote(text)
    return f"No results found for '{text}'. Search directly: {BASE_URL}/?q={encoded}"
