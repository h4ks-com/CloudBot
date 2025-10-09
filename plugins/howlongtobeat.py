# https://howlongtobeat.com - How Long To Beat games
# Author: Matheus Fillipe
# Date: 29/09/2022

import json
import re
from dataclasses import dataclass

import requests

from cloudbot import hook
from cloudbot.util.web import get_session
from cloudbot.util.queue import Queue


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

# Try different potential API endpoints
SEARCH_ENDPOINTS = [
    "https://howlongtobeat.com/api/search",
    "https://howlongtobeat.com/api/find",
]

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://howlongtobeat.com",
    "Referer": "https://howlongtobeat.com/",
    "DNT": "1",
}


@hook.command("hltbn", "hltb_next", autohelp=False)
def hltbn(text, nick, chan):
    """Displays next game in queue for nick."""
    global results_queue

    if text:
        nick = text.strip().split()[0]
        if nick not in results_queue[chan]:
            return f"{nick} has no hltb game in queue."

    if len(results_queue[chan][nick]) == 0:
        return "No [more] results for you"

    game: Game = results_queue[chan][nick].pop()
    return str(game)


def try_api_search(game_name):
    """Try the API search with multiple endpoints"""
    search_payload = {
        "searchType": "games",
        "searchTerms": [game_name],
        "searchPage": 1,
        "size": 20,
        "searchOptions": {
            "games": {
                "userId": 0,
                "platform": "",
                "sortCategory": "popular",
                "rangeCategory": "main",
                "rangeTime": {"min": None, "max": None},
                "gameplay": {"perspective": "", "flow": "", "genre": ""},
                "rangeYear": {"min": "", "max": ""},
                "modifier": "",
            }
        },
    }

    # Try different potential endpoints
    endpoints = [
        "https://howlongtobeat.com/api/search",
        "https://www.howlongtobeat.com/api/search",
        "https://howlongtobeat.com/search",
        "https://www.howlongtobeat.com/search",
    ]

    for endpoint in endpoints:
        try:
            response = get_session().post(
                endpoint, headers=headers, json=search_payload, timeout=10
            )
            if response.ok and response.content:
                data = response.json()
                if data and "data" in data:
                    return parse_api_response(data["data"])
        except (requests.RequestException, ValueError, KeyError):
            continue

    return None


def parse_api_response(data):
    """Parse API response data into Game objects"""
    games = []
    for item in data[:5]:  # Limit to 5 results
        try:
            game = Game(
                name=item.get("game_name", "Unknown"),
                url=f"https://howlongtobeat.com/game/{item.get('game_id', '')}",
                main_story=(
                    f"{float(item.get('comp_main', 0)) / 3600:.1f} Hours"
                    if item.get("comp_main")
                    else "N/A"
                ),
                main_extras=(
                    f"{float(item.get('comp_plus', 0)) / 3600:.1f} Hours"
                    if item.get("comp_plus")
                    else "N/A"
                ),
                completionist=(
                    f"{float(item.get('comp_100', 0)) / 3600:.1f} Hours"
                    if item.get("comp_100")
                    else "N/A"
                ),
            )
            games.append(game)
        except (KeyError, TypeError, ValueError):
            continue
    return games


def extract_game_data_from_json(html):
    """Extract game data from the __NEXT_DATA__ JSON embedded in the page"""
    try:
        # Find the __NEXT_DATA__ script tag
        json_match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
            html,
            re.DOTALL,
        )
        if not json_match:
            return None

        data = json.loads(json_match.group(1))
        game_data = (
            data.get("props", {})
            .get("pageProps", {})
            .get("game", {})
            .get("data", {})
            .get("game", [])
        )

        if not game_data:
            return None

        game = game_data[0]

        # Convert seconds to hours and format
        def format_time(seconds):
            if not seconds or seconds == 0:
                return "N/A"
            hours = seconds / 3600
            if hours == int(hours):
                return f"{int(hours)} Hours"
            else:
                return f"{hours:.1f} Hours"

        return Game(
            name=game.get("game_name", "Unknown"),
            url=f"https://howlongtobeat.com/game/{game.get('game_id', '')}",
            main_story=format_time(game.get("comp_main")),
            main_extras=format_time(game.get("comp_plus")),
            completionist=format_time(game.get("comp_100")),
        )

    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


def scrape_hltb_search(game_name):
    """Search for games using web search"""
    try:
        # Use a simple, reliable search approach
        search_query = f"{game_name} site:howlongtobeat.com/game"

        # Try different search engines with minimal requests
        search_urls = [
            f"https://duckduckgo.com/lite/?q={requests.utils.quote(search_query)}",
            f"https://www.startpage.com/sp/search?query={requests.utils.quote(search_query)}",
        ]

        search_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; bot)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        game_ids = []

        for search_url in search_urls:
            try:
                response = get_session().get(
                    search_url, headers=search_headers, timeout=5
                )
                if response.ok:
                    # Look for HowLongToBeat game URLs in the response
                    game_id_matches = re.findall(
                        r"howlongtobeat\.com/game/(\d+)", response.text
                    )
                    if game_id_matches:
                        # Get up to 5 unique game IDs
                        unique_ids = list(dict.fromkeys(game_id_matches))[:5]
                        game_ids.extend(unique_ids)
                        break
            except (requests.RequestException, requests.Timeout):
                continue

        if not game_ids:
            return None

        # Get game pages for multiple IDs
        games = []
        game_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; bot)",
        }

        for game_id in game_ids:
            try:
                game_url = f"https://howlongtobeat.com/game/{game_id}"
                game_response = get_session().get(
                    game_url, headers=game_headers, timeout=8
                )
                if not game_response.ok:
                    continue

                # Try to extract data from JSON
                game_data = extract_game_data_from_json(game_response.text)
                if game_data:
                    games.append(game_data)
                    continue

                # Fallback: basic title extraction
                title_match = re.search(
                    r"<title>([^|]+)\s*\|\s*HowLongToBeat</title>",
                    game_response.text,
                )
                game_title = (
                    title_match.group(1)
                    .replace("How long is ", "")
                    .replace("?", "")
                    .strip()
                    if title_match
                    else game_name
                )

                games.append(
                    Game(
                        name=game_title,
                        url=game_url,
                        main_story="See website",
                        main_extras="for times",
                        completionist=f"ID: {game_id}",
                    )
                )

            except (requests.RequestException, requests.Timeout):
                continue

        return games if games else None

    except (requests.RequestException, ValueError, AttributeError):
        return None


@hook.command("howlongtobeat", "hltb", autohelp=False)
def howlongtobeat(text, nick, chan):
    """<game> - Search for a game on How Long To Beat"""
    global results_queue

    if not text:
        return "Please provide a game name to search for"

    # Try API search first
    games = try_api_search(text)
    if games:
        results_queue[chan][nick] = games[1:]  # Store all but the first result
        return str(games[0])  # Return the first result directly

    # If API fails, try scraping
    games = scrape_hltb_search(text)
    if games:
        results_queue[chan][nick] = games[1:]  # Store all but the first result
        return str(games[0])  # Return the first result directly

    # If everything fails, provide a direct link
    encoded_search = requests.utils.quote(text)
    return f"No results found for '{text}'. Search directly: https://howlongtobeat.com/?q={encoded_search}"
