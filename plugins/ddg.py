#!/usr/bin/env python3
#
#   DuckDuckGo Search Results API
#
from sys import argv
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

DEFAULT_USER_AGENT = "Mozilla/5.0"

# Extrapolate matching information should the format of the site change.
match = {
    # Main search results body identifier.
    "searchResults": {"id": "links"},
    # Individual search results identifier.
    "result": {"class": "links_main links_deep result__body"},
    # Link and description identifier.
    "link": {"class": "result__snippet"},
}

# Plain HTML Search URL.
searchURL = "https://html.duckduckgo.com/html/?q="


def request(url, headers=None):
    request = Request(url)

    if headers:
        for header in headers:
            request.add_header(header, headers[header])
    else:
        request.add_header("User-Agent", DEFAULT_USER_AGENT)

    try:
        response = urlopen(request)
    except HTTPError as e:
        return e

    return response


def makeSoup(html):
    return BeautifulSoup(html, "lxml")


def parseLink(link):
    url = urlparse(link)
    link = unquote(url.query[5:])
    return link


def search(query):
    query = quote("".join(query))

    response = request(f"{searchURL}{query}")
    soup = makeSoup(response)

    # DuckDuckGo serves a bot-challenge page with no results container to
    # flagged IPs; treat a missing container as an empty result set instead
    # of dereferencing None.
    container = soup.find("div", match["searchResults"])
    if container is None:
        return []

    results = []
    for result in container.find_all("div", match["result"]):
        anch = result.find("a", match["link"])
        if anch is None:
            continue
        link = parseLink(anch["href"])
        url = urlparse(link)
        if "duckduckgo.com" in url.netloc:
            continue
        results.append({"text": anch.text, "url": link})

    return results


if __name__ == "__main__":
    results = search(argv[1:])
    for result in results:
        print(result["text"])
        print(result["url"])
        print("---")
