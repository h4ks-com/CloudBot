import re
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from requests import HTTPError

from cloudbot import hook
from cloudbot.util import web
from cloudbot.util.queue import Queue

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

SEARCH_URL = "https://www.imdb.com/find/"
BASE_URL = "https://www.imdb.com"
REMOVE_REF_PARAMS = ["ref", "ref_"]

results_queue = Queue()


def clean_imdb_url(href: str) -> str:
    """Clean the IMDB URL by removing unwanted query parameters."""
    full_url = BASE_URL + href
    parsed = urlparse(full_url)
    query_params = parse_qs(parsed.query)
    for param in REMOVE_REF_PARAMS:
        query_params.pop(param, None)
    clean_query = urlencode(query_params, doseq=True)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" + (f"?{clean_query}" if clean_query else "")


def extract_imdb_urls(soup: BeautifulSoup) -> list[str]:
    """Extract and clean IMDB URLs from the search results."""
    result_items = soup.select(".find-result-item")
    urls = []
    for result_item in result_items:
        result_link = result_item.select_one("a")
        if result_link:
            href = result_link.get("href")
            if href and isinstance(href, str):
                urls.append(clean_imdb_url(href))
    return urls


def search_imdb(query: str) -> list[str] | None:
    """Search IMDB for movies/shows matching the query"""
    params = {"q": query, "s": "tt", "ttype": "ft"}  # Search for titles  # Feature films

    try:
        response = requests.get(SEARCH_URL, params=params, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

    soup = BeautifulSoup(response.content, "html.parser")
    urls = extract_imdb_urls(soup)

    if not urls:
        return None

    return urls


def get_imdb_info(imdb_url: str) -> dict[str, str] | None:
    """Extract movie information from IMDB page"""
    try:
        response = requests.get(imdb_url, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

    soup = BeautifulSoup(response.content, "html.parser")

    title_elem = soup.select_one(".hero__primary-text")
    if not title_elem:
        title_elem = soup.select_one("h1[data-testid='hero-title-block__title']")
    title = title_elem.text.strip() if title_elem else "Unknown Title"

    year = ""
    title_tag = soup.select_one("title")
    if title_tag:
        year_match = re.search(r"\((\d{4})\)", title_tag.text)
        if year_match:
            year = year_match.group(1)

    rating_elem = soup.select_one("[data-testid='hero-rating-bar__aggregate-rating__score'] span")
    user_score = rating_elem.text.strip() if rating_elem else "N/A"

    critics_score = "N/A"
    metascore_elem = soup.select_one("span.metacritic-score-box")
    if metascore_elem:
        critics_score = metascore_elem.text.strip()

    return {"title": title, "year": year, "user_score": user_score, "critics_score": critics_score, "url": imdb_url}


@hook.command("imdbn", "imdb_next", autohelp=False)
def imdbn(nick, chan, text):
    """Get next IMDB result from your search results."""
    global results_queue
    results = results_queue[chan][nick]
    if results is None or len(results) == 0:
        return "No [more] results found."

    imdb_url = results.pop()
    info = get_imdb_info(imdb_url)
    if not info:
        return "Error retrieving movie information from IMDB"

    short_url = web.try_shorten(info["url"])

    title_year = f"{info['title']}"
    if info["year"]:
        title_year += f" ({info['year']})"

    user_score_text = f"User: {info['user_score']}/10" if info["user_score"] != "N/A" else "User: N/A"
    critics_score_text = f"Critics: {info['critics_score']}/100" if info["critics_score"] != "N/A" else "Critics: N/A"

    return f"\x02{title_year}\x02 - {user_score_text}, {critics_score_text} - {short_url}"


@hook.command("imdb")
def imdb(text: str, nick, chan) -> str:
    """<query> - Search IMDB for movie/show information including ratings"""
    global results_queue
    if not text.strip():
        return "Please provide a movie or show title to search for."

    query = text.strip()

    imdb_urls = search_imdb(query)
    if not imdb_urls:
        return f"No IMDB results found for '{query}'"

    results_queue[chan][nick] = imdb_urls[1:]  # Store all but the first result

    info = get_imdb_info(imdb_urls[0])
    if not info:
        return "Error retrieving movie information from IMDB"

    short_url = web.try_shorten(info["url"])

    title_year = f"{info['title']}"
    if info["year"]:
        title_year += f" ({info['year']})"

    user_score_text = f"User: {info['user_score']}/10" if info["user_score"] != "N/A" else "User: N/A"
    critics_score_text = f"Critics: {info['critics_score']}/100" if info["critics_score"] != "N/A" else "Critics: N/A"

    return f"\x02{title_year}\x02 - {user_score_text}, {critics_score_text} - {short_url}"
