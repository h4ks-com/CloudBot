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

results_queue = Queue()


def search_imdb(query: str, multiple: bool = False) -> str | list[str] | None:
    """Search IMDB for movies/shows matching the query"""
    params = {"q": query, "s": "tt", "ttype": "ft"}  # Search for titles  # Feature films

    try:
        response = requests.get(SEARCH_URL, params=params, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

    soup = BeautifulSoup(response.content, "html.parser")

    # Look for search results with updated selector
    if multiple:
        result_items = soup.select(".find-result-item")[:10]  # Get up to 10 results
        urls = []
        for result_item in result_items:
            result_link = result_item.select_one("a")
            if result_link:
                href = result_link.get("href")
                if href and isinstance(href, str):
                    # Clean the URL by removing unwanted query parameters
                    full_url = BASE_URL + href
                    parsed = urlparse(full_url)
                    query_params = parse_qs(parsed.query)
                    # Remove the ref parameter if it exists
                    query_params.pop("ref", None)
                    clean_query = urlencode(query_params, doseq=True)
                    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" + (f"?{clean_query}" if clean_query else "")
                    urls.append(clean_url)
        return urls if urls else None
    else:
        # Look for the first search result with updated selector
        result_item = soup.select_one(".find-result-item")
        if result_item:
            result_link = result_item.select_one("a")
            if result_link:
                href = result_link.get("href")
                if href and isinstance(href, str):
                    # Clean the URL by removing unwanted query parameters
                    full_url = BASE_URL + href
                    parsed = urlparse(full_url)
                    query_params = parse_qs(parsed.query)
                    # Remove the ref parameter if it exists
                    query_params.pop("ref", None)
                    clean_query = urlencode(query_params, doseq=True)
                    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" + (f"?{clean_query}" if clean_query else "")

    return None


def get_imdb_info(imdb_url: str) -> dict[str, str] | None:
    """Extract movie information from IMDB page"""
    try:
        response = requests.get(imdb_url, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

    soup = BeautifulSoup(response.content, "html.parser")

    # Extract title
    title_elem = soup.select_one(".hero__primary-text")
    if not title_elem:
        title_elem = soup.select_one("h1[data-testid='hero-title-block__title']")
    title = title_elem.text.strip() if title_elem else "Unknown Title"

    # Extract year from page title
    year = ""
    title_tag = soup.select_one("title")
    if title_tag:
        year_match = re.search(r"\((\d{4})\)", title_tag.text)
        if year_match:
            year = year_match.group(1)

    # Extract IMDB rating (user score)
    rating_elem = soup.select_one("[data-testid='hero-rating-bar__aggregate-rating__score'] span")
    user_score = rating_elem.text.strip() if rating_elem else "N/A"

    # Extract Metascore (critics score)
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

    # Get the next URL and fetch its info
    imdb_url = results.pop()
    info = get_imdb_info(imdb_url)
    if not info:
        return "Error retrieving movie information from IMDB"

    # Shorten the URL
    short_url = web.try_shorten(info["url"])

    # Format the response
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

    # Search for multiple results to populate the queue
    imdb_urls = search_imdb(query, multiple=True)
    if not imdb_urls:
        return f"No IMDB results found for '{query}'"

    # Store the results in the queue for this user/channel
    results_queue[chan][nick] = imdb_urls[1:]  # Store all but the first result

    # Get detailed information for the first result
    info = get_imdb_info(imdb_urls[0])
    if not info:
        return "Error retrieving movie information from IMDB"

    # Shorten the URL
    short_url = web.try_shorten(info["url"])

    # Format the response
    title_year = f"{info['title']}"
    if info["year"]:
        title_year += f" ({info['year']})"

    user_score_text = f"User: {info['user_score']}/10" if info["user_score"] != "N/A" else "User: N/A"
    critics_score_text = f"Critics: {info['critics_score']}/100" if info["critics_score"] != "N/A" else "Critics: N/A"

    return f"\x02{title_year}\x02 - {user_score_text}, {critics_score_text} - {short_url}"
