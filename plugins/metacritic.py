import json
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from cloudbot import hook
from cloudbot.util.queue import Queue
from cloudbot.util.web import get_session

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}
URL = "https://www.metacritic.com/search"
CATEGORY_MAP = {"all": None, "games": 13, "movies": 2, "shows": 1, "people": 3}
NUMBER_OF_RESULTS = 3


@dataclass
class SearchResult:
    url: str
    title: Optional[str]
    platform: Optional[str]
    release_date: Optional[str]
    meta_score: Optional[str]
    user_score: Optional[str]

    @classmethod
    def from_url(cls, url: str) -> "SearchResult":
        response = get_session().get(url, headers=HEADERS)
        soup = BeautifulSoup(response.content, "html.parser")

        title_elem = soup.select_one("h1")
        title = title_elem.text.strip() if title_elem else None

        # Metascore and release date come from JSON-LD structured data
        meta_score = None
        release_date = None
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                ld = json.loads(script.string or "")
                if ld.get("aggregateRating"):
                    meta_score = str(ld["aggregateRating"].get("ratingValue"))
                if ld.get("datePublished"):
                    release_date = ld["datePublished"]
                break
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        # User score: first occurrence is the main user score
        user_elem = soup.select_one("div.c-siteReviewScore_background-user")
        user_score = user_elem.text.strip() if user_elem else None

        return SearchResult(
            url=url,
            title=title,
            platform=None,
            release_date=release_date,
            meta_score=meta_score,
            user_score=user_score,
        )


def search_metacritic(query, category=None) -> List[str]:
    encoded_query = quote(query)
    url = f"{URL}/{encoded_query}"
    if category:
        url += f"?category={category}"

    response = get_session().get(url, headers=HEADERS)
    soup = BeautifulSoup(response.content, "html.parser")

    results = soup.select("div.c-pageSiteSearch-results a[href]")
    result_urls = [
        f"https://www.metacritic.com{link['href']}" for link in results
    ]

    return result_urls


@lru_cache
def get_queue():
    return Queue()


@hook.command("metan", autohelp=False)
def metan(text, chan, nick):
    """[nick] - gets the next result from the last metacritic search"""
    args = text.strip().split()
    if len(args) > 0:
        nick = args[0]

    results_queue = get_queue()
    urls = results_queue[chan][nick]
    if len(urls) == 0:
        return "No [more] results found for " + nick

    results = [
        SearchResult.from_url(urls.pop()) for _ in range(min(NUMBER_OF_RESULTS, len(urls)))
    ]

    return [
        f"\x02{result.title or '?'}\x02{f' ({result.platform})' if result.platform else ''} - \x02Release\x02: {result.release_date or '?'} "
        f"- \x02Metascore:\x02 {result.meta_score or '?'} - \x02User Score:\x02 {result.user_score or '?'} - {result.url or '?'}"
        for result in results
    ]


@hook.command("metacritic", "meta")
def metacritic(text, reply, chan, nick):
    """[list|all|games|movies|shows|people] <title> - gets rating for <title> from
    metacritic on the specified catetory"""
    results_queue = get_queue()
    args = text.strip()

    all_platforms = list(CATEGORY_MAP.keys())
    if args.casefold() == "list".casefold():
        return "Categoties: {}".format(", ".join(all_platforms))

    first = args.split()[0]
    category = None
    query = args
    if first in CATEGORY_MAP:
        category = CATEGORY_MAP[first]
        query = " ".join(args.split()[1:])

    results_queue[chan][nick] = search_metacritic(query, category)
    return metan("", chan, nick)


if __name__ == "__main__":
    query = "Final Fantasy"
    category = CATEGORY_MAP.get(
        "games"
    )  # Change this to test different categories
    urls = search_metacritic(query, category)
    for url in urls:
        result = SearchResult.from_url(url)
        print(result)
