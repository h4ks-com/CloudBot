import json
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from cloudbot import hook
from cloudbot.util.web import get_session
from cloudbot.util import web


def scrape_rotten_tomatoes(movie_title: str) -> dict[str, Any] | None:
    search_url = "https://www.rottentomatoes.com/search"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = get_session().get(
            search_url,
            params={"search": movie_title},
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        movie_links = soup.find_all("a", href=re.compile(r"/m/"))

        if not movie_links:
            return None

        best_match = _find_best_movie_match(movie_links, movie_title)
        movie_url = _construct_movie_url(best_match["href"])

        movie_response = get_session().get(movie_url, headers=headers, timeout=10)
        movie_response.raise_for_status()

        movie_soup = BeautifulSoup(movie_response.content, "html.parser")

        movie_data = _extract_movie_data(movie_soup, movie_title)
        movie_data["url"] = movie_url

        return movie_data

    except (requests.RequestException, ValueError):
        return None


def _find_best_movie_match(movie_links: list, movie_title: str):
    movie_title_lower = movie_title.lower()

    # Try exact title match first
    for link in movie_links:
        link_text = link.get_text().strip().lower()
        parent_text = (
            link.parent.get_text().strip().lower() if link.parent else ""
        )

        if movie_title_lower in link_text or movie_title_lower in parent_text:
            return link

    # Fallback to word matching for complex titles
    significant_words = [
        word for word in movie_title_lower.split() if len(word) > 3
    ]
    if len(significant_words) >= 2:
        for link in movie_links:
            link_text = link.get_text().strip().lower()
            parent_text = (
                link.parent.get_text().strip().lower() if link.parent else ""
            )

            link_matches = sum(
                1 for word in significant_words if word in link_text
            )
            parent_matches = sum(
                1 for word in significant_words if word in parent_text
            )

            if (
                link_matches >= len(significant_words) // 2
                or parent_matches >= len(significant_words) // 2
            ):
                return link

    return movie_links[0]


def _construct_movie_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return "https://www.rottentomatoes.com" + href


def _extract_movie_data(
    movie_soup: BeautifulSoup, fallback_title: str
) -> dict[str, Any]:
    movie_data = {}

    # Extract title and year from JSON-LD script tags
    script_tags = movie_soup.find_all("script", type="application/ld+json")
    for script in script_tags:
        try:
            json_data = json.loads(script.string)
            if "aggregateRating" in json_data:
                movie_data["title"] = json_data.get("name", fallback_title)
                date_published = json_data.get("datePublished", "")
                movie_data["year"] = (
                    date_published[:4] if date_published else ""
                )
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    # Extract scores from specific rt-text elements
    critics_score_element = movie_soup.find("rt-text", slot="criticsScore")
    if critics_score_element:
        critics_text = critics_score_element.get_text().strip()
        critics_match = re.search(r"(\d+)%", critics_text)
        if critics_match:
            movie_data["tomatometer"] = int(critics_match.group(1))

    audience_score_element = movie_soup.find("rt-text", slot="audienceScore")
    if audience_score_element:
        audience_text = audience_score_element.get_text().strip()
        audience_match = re.search(r"(\d+)%", audience_text)
        if audience_match:
            movie_data["audience_score"] = int(audience_match.group(1))

    # Fallback title extraction
    if "title" not in movie_data:
        title_element = movie_soup.find("h1") or movie_soup.find("title")
        if title_element:
            movie_data["title"] = (
                title_element.get_text().strip().split(" - ")[0]
            )

    return movie_data


@hook.command("rottentomatoes", "rt")
def rotten_tomatoes(text: str, bot, reply) -> str:
    """<title> - gets ratings for <title> from Rotten Tomatoes"""
    title = text.strip()
    if not title:
        return "Please provide a movie title"

    movie_data = scrape_rotten_tomatoes(title)

    if not movie_data:
        return f"No Rotten Tomatoes data found for '{title}'"

    display_title = movie_data.get("title", title)
    result_parts = [f"\x02{display_title}\x02"]

    tomatometer = movie_data.get("tomatometer")
    if tomatometer is not None:
        result_parts.append(f"Critics: \x02{tomatometer}%\x02")

    audience_score = movie_data.get("audience_score")
    if audience_score is not None:
        result_parts.append(f"Audience: \x02{audience_score}%\x02")

    year = movie_data.get("year")
    if year:
        result_parts.append(f"({year})")

    movie_url = movie_data.get("url", "")
    if movie_url:
        shortened_url = web.try_shorten(movie_url)
        result_parts.append(shortened_url)

    if len(result_parts) > 1:
        return " - ".join(result_parts)
    else:
        return f"Found '{display_title}' but no ratings available"
