"""Linux man pages from man.archlinux.org.

The site renders every page as plain text at a predictable URL and resolves a
bare name to its lowest section on its own, so a lookup is one request with no
search step and no HTML to parse.
"""

import re
from urllib.parse import quote

import requests

from cloudbot import hook

MAN_URL = "https://man.archlinux.org/man"
SEARCH_URL = "https://man.archlinux.org/search"
TIMEOUT = 15
SUMMARY_CHARS = 260
SYNOPSIS_CHARS = 120

# Every section body is indented under a flush-left heading.
SECTION_RE = re.compile(r"^(\w[\w ]*)\n((?:[ \t]+.*\n|\n)*)", re.MULTILINE)


def section(page: str, name: str) -> list[str]:
    """Return the lines of one man page section, indentation stripped."""
    for heading, body in SECTION_RE.findall(page):
        if heading.strip().upper() == name:
            return [line.strip() for line in body.splitlines() if line.strip()]
    return []


def clip(line: str, limit: int) -> str:
    """Shorten to limit without cutting a word in half."""
    if len(line) <= limit:
        return line
    return line[:limit].rsplit(" ", 1)[0] + "..."


def fetch(page: str) -> str:
    """Return the plain text of a man page, or an empty string when there is none."""
    response = requests.get(f"{MAN_URL}/{quote(page)}.txt", timeout=TIMEOUT)
    if response.status_code == 404:
        return ""
    response.raise_for_status()
    return response.text


@hook.command("man")
def man(text: str) -> str:
    """<page[.section]> - looks up a Linux man page. e.g. .man grep, .man printf.3, .man systemd.service"""
    words = text.split()
    if not words:
        return "Usage: .man <page[.section]>"

    page = words[0]
    try:
        body = fetch(page)
    except requests.RequestException as e:
        return f"man.archlinux.org error: {e}"

    if not body:
        return (
            f"No man page for '{page}'. "
            f"Search: {SEARCH_URL}?q={quote(page)}"
        )

    title = body.split("\n", 1)[0].split("  ")[0].strip() or page
    summary = section(body, "NAME") or section(body, "DESCRIPTION")
    synopsis = section(body, "SYNOPSIS")

    out = clip(f"{title}: {' '.join(summary)}", SUMMARY_CHARS)
    if synopsis:
        out += f" | {clip(synopsis[0], SYNOPSIS_CHARS)}"
    return f"{out} | {MAN_URL}/{quote(page)}"
