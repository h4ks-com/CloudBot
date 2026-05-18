# Search code on GitHub via REST search/code API.
# Replaces grep.app which is now blocked by a Vercel security checkpoint.
# Author: Matheus Fillipe (rewrite 2026-05-04)

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from cloudbot import hook
from cloudbot.util.queue import Queue

API = "https://api.github.com/search/code"


@dataclass
class Result:
    url: str
    lines: list


results_queue = Queue()


def _build_query(text: str, lang: list[str] | None, words: bool) -> str:
    q = text.strip()
    if words:
        q = f'"{q}"'
    if lang:
        q += " " + " ".join(f"language:{l}" for l in lang)
    return q


def _format_fragment(fragment: str) -> list[str]:
    lines = []
    for line in fragment.splitlines():
        line = line.rstrip()
        if line.strip():
            lines.append(line[:300])
    return lines[:8]


def grep(query: str, token: str, **params) -> tuple[list, list]:
    lang = params.get("f.lang")
    words = params.get("words") == "true"
    q = _build_query(query, lang if isinstance(lang, list) else None, words)

    headers = {
        "Accept": "application/vnd.github.text-match+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(
        API, params={"q": q, "per_page": "30"}, headers=headers, timeout=10
    )
    response.raise_for_status()
    obj = response.json()

    results = []
    for item in obj.get("items", []):
        url = item.get("html_url", "")
        matches = item.get("text_matches", [])
        if matches:
            lines = _format_fragment(matches[0].get("fragment", ""))
        else:
            lines = [item.get("path", "")]
        results.append(Result(url, lines))

    return results, []


@hook.command("gitgrepn", "grepn", autohelp=False)
def gitnext(text, reply, chan, nick) -> str | None:
    """Gets next result in gitgrep."""
    results = results_queue[chan][nick]
    user = text.strip().split()[0] if text.strip() else ""
    if user:
        if user in results_queue[chan]:
            results = results_queue[chan][user]
        else:
            return f"Nick '{user}' has no queue."

    if len(results) == 0:
        return "No [more] results found."

    r = results.pop()
    for line in [line for line in r.lines[:3] if line.strip()]:
        reply(line)
    reply(f"-->  {r.url}")
    return None


@hook.command("gitgrep", "grep")
def gitgrep(text, bot, reply, chan, nick):
    """[args] <query> - Search code on GitHub.
    Optional flags: -l <lang> (filter by language, repeatable), -w (match whole phrase).
    -e (regex) and -i (case) are no-ops: GitHub code search is case-insensitive and
    does not support regex.
    """
    params: dict = {}

    def findargs(text):
        text = text.strip()
        match = re.match(r"^-l\s+(\S+)", text)
        start = 0
        if match:
            params.setdefault("f.lang", []).append(match[1])
            start = match.end()
        elif re.search(r"^-w\s+", text):
            params["words"] = "true"
            start = 3
        elif re.search(r"^-[ie]\s+", text):
            start = 3
        if start == 0:
            return text
        return findargs(text[start:])

    text = findargs(text)
    if not text.strip():
        return "Usage: .grep [-l lang] [-w] <query>"

    token = bot.config.get_api_key("github") or ""
    if not token:
        return "GitHub PAT not configured (api_keys.github)."

    try:
        results, _ = grep(text, token=token, **params)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 403:
            return "GitHub rate-limited or PAT lacks scope. Try again later."
        if status == 422:
            return "Bad query (GitHub rejected it). Simplify the search terms."
        return f"GitHub API error ({status})."
    except requests.RequestException as e:
        return f"Network error: {e}"

    if len(results) == 0:
        return "No results found."

    results_queue[chan][nick] = results
    return gitnext("", reply, chan, nick)
