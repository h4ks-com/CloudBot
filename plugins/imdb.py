import re

import requests
from bs4 import BeautifulSoup
from requests import HTTPError

from cloudbot import hook
from cloudbot.util import web

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

SEARCH_URL = "https://www.imdb.com/find/"
BASE_URL = "https://www.imdb.com"


def search_imdb(query):
    """Search IMDB for movies/shows matching the query"""
    params = {"q": query, "s": "tt", "ttype": "ft"}  # Search for titles  # Feature films

    try:
        response = requests.get(SEARCH_URL, params=params, headers=HEADERS)
        response.raise_for_status()
    except HTTPError:
        return None

    soup = BeautifulSoup(response.content, "html.parser")

    # Look for the first search result with updated selector
    result_item = soup.select_one(".find-result-item")
    if result_item:
        result_link = result_item.select_one("a")
        if result_link:
            return BASE_URL + result_link.get("href")

    return None


def get_imdb_info(imdb_url):
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


@hook.command("imdb")
def imdb(text, reply):
    """<query> - Search IMDB for movie/show information including ratings"""
    if not text.strip():
        return "Please provide a movie or show title to search for."

    query = text.strip()

    # Search for the movie/show
    imdb_url = search_imdb(query)
    if not imdb_url:
        return f"No IMDB results found for '{query}'"

    # Get detailed information
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
