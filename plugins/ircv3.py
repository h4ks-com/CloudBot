"""
ircv3.py

Searches the issues and pull requests of the IRCv3 ideas and specification
repositories on GitHub.

Usage:
    .ircv3 <query>   (aliases: .ircv3search, .ircv3issues)
"""

from typing import Annotated

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.hook import Arg
from cloudbot.util import formatting
from cloudbot.util.web import get_session

SEARCH_URL = "https://api.github.com/search/issues"
REPOS = ("ircv3/ircv3-ideas", "ircv3/ircv3-specification")
MAX_RESULTS = 3


def build_query(query):
    """
    >>> build_query("batch")
    'batch repo:ircv3/ircv3-ideas repo:ircv3/ircv3-specification'
    """
    repo_quals = " ".join(f"repo:{repo}" for repo in REPOS)
    return f"{query} {repo_quals}"


def format_item(item):
    """Format one search hit as: PR|Issue repo#number [state] url | title"""
    kind = "PR" if "pull_request" in item else "Issue"
    repo = item["repository_url"].split("/repos/")[-1]
    title = formatting.truncate(item["title"], 100)
    return f"{kind} {repo}#{item['number']} [{item['state']}] {item['html_url']} | {title}"


@hook.command("ircv3", "ircv3search", "ircv3issues")
def ircv3_cmd(bot: CloudBot, query: Annotated[str, Arg("Text to search for")]):
    """searches issues and PRs in the ircv3-ideas and ircv3-specification repos"""
    headers = {"Accept": "application/vnd.github+json"}
    token = bot.config.get("api_keys", {}).get("github", None)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = get_session().get(
        SEARCH_URL,
        params={
            "q": build_query(query),
            "sort": "updated",
            "order": "desc",
            "per_page": MAX_RESULTS,
        },
        headers=headers,
        timeout=15,
    )

    if r.status_code != 200:
        return f"GitHub search failed (HTTP {r.status_code})."

    data = r.json()
    items = data.get("items", [])
    total = data.get("total_count", 0)

    if not items:
        return (
            "No matching issues or PRs in ircv3-ideas or ircv3-specification."
        )

    lines = [format_item(item) for item in items[:MAX_RESULTS]]
    if total > MAX_RESULTS:
        lines.append(
            f"{total} matches in total, showing the {MAX_RESULTS} most recently updated."
        )
    return lines
