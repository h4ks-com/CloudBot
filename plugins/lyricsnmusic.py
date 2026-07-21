"""Lyrics lookup over free, keyless sources.

Genius' public search endpoint resolves a song name and a bare lyric fragment
to the same result shape, so one call answers both "get me this song" and
"what song goes like this", and its catalogue tracks current releases. Lyrics
come off the matched Genius page; api.lyrics.ovh is the fallback for when that
markup shifts under us.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote

import requests

from cloudbot import hook
from cloudbot.util import web
from cloudbot.util.http import parse_soup
from cloudbot.util.queue import Queue, UserQueue

GENIUS_SEARCH_API = "https://genius.com/api/search/multi"
LYRICS_OVH_API = "https://api.lyrics.ovh/v1"

# Genius serves both its search endpoint and its pages only to browser agents.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SEARCH_TIMEOUT = 10
PAGE_TIMEOUT = 20

pending = Queue()
listed = Queue()

PAGE_SIZE = 3
SNIPPET_CHARS = 110
PREVIEW_CHARS = 200


class LyricsError(Exception):
    """An upstream lyrics service failed or returned something unusable."""


@dataclass
class Song:
    artist: str
    title: str
    url: str = ""
    snippet: str = ""

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}"

    @property
    def key(self) -> tuple[str, str]:
        return self.artist.strip().lower(), self.title.strip().lower()


def search(query: str) -> list[Song]:
    """Find songs by name, by "artist - title", or by a fragment of the lyrics."""
    query = query.replace(" - ", " ").strip()
    if not query:
        return []

    try:
        response = requests.get(
            GENIUS_SEARCH_API,
            params={"q": query},
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=SEARCH_TIMEOUT,
        )
        response.raise_for_status()
        sections = response.json()["response"]["sections"]
    except requests.RequestException as e:
        raise LyricsError(f"song search failed: {e}") from e
    except (ValueError, KeyError) as e:
        raise LyricsError("song search returned an unexpected response") from e

    songs: dict[tuple[str, str], Song] = {}
    for section in sections:
        for hit in section.get("hits") or []:
            song = _from_genius(hit)
            if song and song.key not in songs:
                songs[song.key] = song
    return list(songs.values())


def _from_genius(hit: dict) -> Song | None:
    result = hit.get("result") or {}
    if result.get("_type") != "song":
        return None
    artist = (result.get("primary_artist") or {}).get("name", "").strip()
    title = (result.get("title") or "").strip()
    url = result.get("url") or ""
    # Genius files playlists and album annotations as songs; only real lyric
    # pages get the -lyrics suffix.
    if not artist or not title or not url.endswith("-lyrics"):
        return None
    return Song(artist=artist, title=title, url=url, snippet=_snippet(hit))


def _snippet(hit: dict) -> str:
    for highlight in hit.get("highlights") or []:
        if highlight.get("property") == "lyrics":
            value = highlight.get("value") or ""
            return " / ".join(
                line for line in value.split("\n") if line.strip()
            )
    return ""


def fetch_lyrics(song: Song) -> str:
    """Return the full plain lyrics for a song, or an empty string when unknown."""
    scraped = _scrape_genius(song.url) if song.url else ""
    return scraped or _lyrics_ovh(song.artist, song.title)


@lru_cache(maxsize=128)
def _scrape_genius(url: str) -> str:
    """Genius pages are ~500KB each, so keep the parsed text around."""
    try:
        response = requests.get(
            url, headers={"User-Agent": BROWSER_UA}, timeout=PAGE_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as e:
        raise LyricsError(f"could not read {url}: {e}") from e

    return extract_genius_lyrics(response.text)


def extract_genius_lyrics(html: str) -> str:
    soup = parse_soup(html)
    for header in soup.select('[class*="LyricsHeader"]'):
        header.decompose()
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    blocks = [
        block.get_text()
        for block in soup.select('[data-lyrics-container="true"]')
    ]
    return "\n".join(block for block in blocks if block.strip()).strip()


@lru_cache(maxsize=128)
def _lyrics_ovh(artist: str, title: str) -> str:
    try:
        response = requests.get(
            f"{LYRICS_OVH_API}/{quote(artist)}/{quote(title)}",
            timeout=SEARCH_TIMEOUT,
        )
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        return (response.json().get("lyrics") or "").strip()
    except requests.RequestException as e:
        raise LyricsError(f"lyrics lookup failed: {e}") from e
    except ValueError as e:
        raise LyricsError(
            "lyrics lookup returned an unexpected response"
        ) from e


def format_hit(index: int, song: Song) -> str:
    parts = [f"{index}) {song.label}"]
    if song.snippet:
        parts.append(f'"{song.snippet[:SNIPPET_CHARS]}"')
    if song.url:
        parts.append(song.url)
    return " - ".join(parts)


def take_page(queue: UserQueue) -> list[Song]:
    """Pop up to a page off the queue. Concurrent callers share it, so pop until it raises."""
    page: list[Song] = []
    while len(page) < PAGE_SIZE:
        try:
            page.append(queue.pop(0))
        except IndexError:
            break
    return page


def show_page(
    chan: str, owner: str, nick: str, reply: Callable[..., None]
) -> str | None:
    queue = pending[chan][owner]
    page = take_page(queue)
    if not page:
        return "No [more] results found."

    listed[chan][nick] = page
    reply(*[format_hit(i, song) for i, song in enumerate(page, 1)])
    if queue:
        reply(
            f"{len(queue)} more - .lyricsn to page, .getlyrics <n> for the words"
        )
    return None


@hook.command("lyrics", "ly", "lysearch", "lys")
def lyrics(
    text: str, chan: str, nick: str, reply: Callable[..., None]
) -> str | None:
    """<artist song|lyric> - finds a song by name or by a line of its lyrics. Use .getlyrics <n> for the words, .lyricsn to page."""
    try:
        results = search(text)
    except LyricsError as e:
        return f"Lyrics service error: {e}"

    if not results:
        return f"Nothing found for '{text}'."

    pending[chan][nick] = results
    return show_page(chan, nick, nick, reply)


@hook.command("lyricsn", "lyn", autohelp=False)
def lyricsn(
    text: str, chan: str, nick: str, reply: Callable[..., None]
) -> str | None:
    """[nick] - shows the next page of results for nick, or for you by default."""
    owner = text.strip().split()[0] if text.strip() else nick
    if owner != nick and owner not in pending[chan]:
        return f"Nick '{owner}' has no queue."

    return show_page(chan, owner, nick, reply)


@hook.command("getlyrics", "gly")
def getlyrics(text: str, chan: str, nick: str) -> str:
    """<n> - fetches the full lyrics of the nth song on the last list."""
    page = listed[chan][nick]
    if not page:
        return "Nothing listed yet. Run .lyrics first."

    choice = text.strip()
    if not choice.isdecimal() or not 1 <= int(choice) <= len(page):
        return f"Pick a number between 1 and {len(page)}."

    song = page[int(choice) - 1]
    try:
        words = fetch_lyrics(song)
    except LyricsError as e:
        return f"Lyrics service error: {e}"

    if not words:
        return f"No lyrics available for {song.label}."

    preview = " / ".join(line for line in words.splitlines() if line.strip())
    url = web.paste(f"{song.label}\n\n{words}", ext="txt")
    return f"{song.label}: {preview[:PREVIEW_CHARS]}... full: {url}"
