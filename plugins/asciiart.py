# Search and display ascii art
# Author: Matheus Fillipe
# Date: 25/09/2022

import json
import string
from dataclasses import dataclass, field
from random import choice
from time import time

from bs4 import BeautifulSoup, Tag

from cloudbot.util.web import get_session

if __name__ != "__main__":
    from cloudbot import hook

URL = "https://www.asciiart.eu"
FILE = "plugins/asciiart.json"
THRESHOLD = 0.7
MAX_PER_MINUTE = 2
MAX_PER_HOUR = 5


@dataclass
class Page:
    name: str
    aliases: list[str]
    url: str
    arts: list[str]


@dataclass
class Directory:
    name: str
    url: str
    children: list[Page] | list["Directory"] = field(default_factory=list)


def str_similarity(a, b):
    a, b = a.lower(), b.lower()
    error = 0
    for i in string.ascii_lowercase:
        error += abs(a.count(i) - b.count(i))
    total = len(a) + len(b)
    return (total - error) / total


def find_directories(url: str) -> list[Directory]:
    r = get_session().get(url)
    if not r.ok:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    root = soup.find("div", {"class": "directory-columns"})
    if not isinstance(root, Tag):
        return []

    directories = []
    for link in root.find_all("li"):
        if not isinstance(link, Tag):
            continue
        a = link.find("a")
        if not isinstance(a, Tag):
            continue
        href = a.get("href", "")
        if not isinstance(href, str):
            continue
        directories.append(Directory(a.text, URL + href))

    return directories


def scrape_page(url: str) -> Page | None:
    r = get_session().get(url)
    if not r.ok:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    artlist = soup.find("div", {"class": "asciiarts"})
    if not isinstance(artlist, Tag):
        return None

    header = soup.find("div", {"class": "bg-header"})
    aliases: list[str] = []
    if isinstance(header, Tag):
        aliases = header.find_all("pre")[-1].text.split(" - ")

    arts = []
    for art in artlist.find_all("pre"):
        arts.append(art.text)

    title = soup.title.text if soup.title else ""
    return Page(title, aliases, url, arts)


def generate_map(url: str = URL) -> Directory:
    directories = find_directories(url)
    if not directories:
        page = scrape_page(url)
        pages: list[Page] = [page] if page else []
        return Directory("root", url, pages)

    for directory in directories:
        print(f"Scraping {directory.name}")
        page = scrape_page(directory.url)
        if page:
            new_children: list[Page] = [page]
            directory.children = new_children
        else:
            sub = generate_map(directory.url)
            directory.children = sub.children

    return Directory("root", URL, directories)


def dir2dict(directory: Directory) -> dict:
    """Converts a Directory object to a json serializable dict."""
    d: dict = {}
    for dir in directory.children or []:
        if isinstance(dir, Directory):
            d[dir.name.lower().replace("@", "")] = dir2dict(dir)
        else:
            d[",".join(dir.aliases)] = dir.arts

    return d


def save_map(filename):
    with open(filename, "w", encoding="utf-8") as fh:
        json.dump(dir2dict(generate_map()), fh, indent=4)


def load_map(filename):
    with open(FILE, encoding="utf-8") as fh:
        return json.load(fh)


if __name__ != "__main__":
    asciimap = load_map(FILE)

    uses: dict[str, list[float]] = {}

    @hook.command("asciiart", "aa", autohelp=False)
    def asciiart(text, reply, chan):
        """<search> - Search for ascii art."""
        if chan not in uses:
            uses[chan] = []

        last_hour_uses = sum(1 for i in uses[chan] if i > time() - 3600)
        last_minute_uses = sum(1 for i in uses[chan] if i > time() - 60)

        if last_hour_uses >= MAX_PER_HOUR:
            return "The command has reached the maximum uses per hour."
        if last_minute_uses >= MAX_PER_MINUTE:
            return "The command has reached the maximum uses per minute."

        uses[chan].append(time())

        if not text:
            return "Usage: .asciiart <search>"

        query = text.lower()

        def sublists(d: dict):
            """All lists inside a dict merged."""
            if isinstance(d, list):
                return d

            if d is None:
                return []

            acc = []
            for v in d.values():
                if isinstance(v, dict):
                    acc.extend(sublists(v))
                elif isinstance(v, list):
                    acc.extend(v)
            return acc

        def find_query(query, map):
            matches = []
            for key, value in map.items():
                if "," in key:
                    for alias in key.split(","):
                        s = str_similarity(alias, query)
                        if s > THRESHOLD:
                            matches.append({s: value})

                s = str_similarity(key, query)
                if s > THRESHOLD:
                    matches.append({s: value})

                if isinstance(value, dict):
                    matches.extend(find_query(query, value))
            return matches

        matches = find_query(query, asciimap)
        value = max(matches, key=lambda x: list(x.keys())[0])
        for line in choice(sublists(value)).split("\n"):
            reply(line)


if __name__ == "__main__":
    save_map(FILE)
