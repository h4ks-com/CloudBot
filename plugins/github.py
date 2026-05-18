import re
import shlex
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime

import pytz
from github import Auth, Github, GithubException
from requests import HTTPError

from cloudbot import hook
from cloudbot.util import colors, formatting, web
from cloudbot.util.web import get_session

shortcuts = {}
url_re = re.compile(
    r"(?:https?://github\.com/)?(?P<owner>[^/]+)/(?P<repo>[^/]+)"
)


def parse_url(url):
    """
    >>> parse_url("https://github.com/TotallyNotRobots/CloudBot/")
    ('TotallyNotRobots', 'CloudBot')
    >>> parse_url("TotallyNotRobots/CloudBot/")
    ('TotallyNotRobots', 'CloudBot')
    >>> parse_url("TotallyNotRobots/CloudBot")
    ('TotallyNotRobots', 'CloudBot')
    """
    match = url_re.match(url)
    return match.group("owner"), match.group("repo")


@hook.on_start()
def load_shortcuts(bot):
    shortcuts["cloudbot"] = parse_url(bot.repo_link)


@hook.command("ghissue", "issue")
def issue_cmd(text, event):
    """<username|repo> [number] - gets issue [number]'s summary, or the open issue count if no issue is specified"""
    args = text.split()
    first = args[0]
    shortcut = shortcuts.get(first)
    if shortcut:
        data = shortcut
    else:
        data = parse_url(first)

    owner, repo = data
    issue = args[1] if len(args) > 1 else None

    if issue:
        r = get_session().get(
            "https://api.github.com/repos/{}/{}/issues/{}".format(
                owner, repo, issue
            )
        )

        try:
            r.raise_for_status()
        except HTTPError as err:
            if err.response.status_code == 404:
                return f"Issue #{issue} doesn't exist in {owner}/{repo}"

            event.reply(str(err))
            raise

        j = r.json()

        url = web.try_shorten(j["html_url"], service="git.io")
        number = j["number"]
        title = j["title"]
        summary = formatting.truncate(j["body"].split("\n")[0], 25)
        if j["state"] == "open":
            state = "\x033\x02Opened\x02\x0f by {}".format(j["user"]["login"])
        else:
            state = "\x034\x02Closed\x02\x0f by {}".format(
                j["closed_by"]["login"]
            )

        return "Issue #{} ({}): {} | {}: {}".format(
            number, state, url, title, summary
        )

    r = get_session().get(f"https://api.github.com/repos/{owner}/{repo}/issues")

    r.raise_for_status()
    j = r.json()

    count = len(j)
    if count == 0:
        return "Repository has no open issues."

    return f"Repository has {count} open issues."


@dataclass
class Result:
    header: str
    summary: str
    bottom: str | None = None

    def as_list(self):
        summary = [
            formatting.truncate(line, 420)
            for line in (self.summary or "").split("\n")
        ][:6]
        return (
            [formatting.truncate(f"\x02{self.header}\x02", 420)]
            + summary
            + [self.bottom] * bool(self.bottom)
        )


def format_date(date: datetime) -> str:
    """Format a datetime to either %d minutes ago %d hours ago, %d days ago or a formatted date without time."""
    now = datetime.now(pytz.utc)
    delta = now - date
    if delta.days > 0:
        return date.strftime("%Y-%m-%d")
    if delta.seconds < 60:
        return f"{delta.seconds} seconds ago"
    if delta.seconds < 3600:
        return f"{delta.seconds // 60} minutes ago"
    return f"{delta.seconds // 3600} hours ago"


def remove_qualifiers(query: str) -> str:
    args = query.split(" ")
    i = 0
    for i, arg in enumerate(args):
        if ":" not in arg:
            break
    return " ".join(args[i:])


def search_code(g: Github, query: str) -> Generator[Result, None, None]:
    """<query> - Searches for code matching the query on GitHub."""
    for result in g.search_code(query):
        content = result.decoded_content.decode("utf-8").split("\n")

        i = 0
        needle = remove_qualifiers(query).lower()
        for i, line in enumerate(content):
            if needle in line.lower():
                content[i] = f"{colors.get_color('green')}{line}"
                break
        else:
            i = -1

        i += 1
        yield Result(
            f"{result.repository.full_name} - {result.path}",
            "\n".join(content[i - 4 : i + 4]),
            result.html_url
            + f"#L{i}" * (i > 0)
            + f"    \x02\x1d{result.repository.language}\x1d",
        )


def search_issues(g: Github, query: str) -> Generator[Result, None, None]:
    """<query> - Searches for issues matching the query on GitHub."""
    for result in g.search_issues(query):
        yield Result(
            result.title,
            result.body,
            result.html_url,
        )


def search_repo(g: Github, query: str) -> Generator[Result, None, None]:
    """<query> - Searches for repositories matching the query on GitHub."""
    for result in g.search_repositories(query):
        commit = None
        for commit in result.get_commits(result.default_branch):
            break
        br = "\n"
        yield Result(
            f"{result.html_url} 📄\x02\x1d{result.language}\x1d ⭐{result.stargazers_count}",
            (
                formatting.truncate(
                    f"\x1d{commit.commit.message.strip().replace(br, ' ')}\x1d ⏲️ \x02{format_date(commit.commit.author.date)}\x02"
                )
            )
            * bool(commit)
            + f"\n{result.description}",
        )


def search_user(g: Github, query: str) -> Generator[Result, None, None]:
    """<query> - Searches for users matching the query on GitHub."""
    for result in g.search_users(query):
        yield Result(
            f"{result.name} 🌎\x02\x1d{result.location}\x1d - \x02{result.followers}\x02 followers and \x02{result.public_repos}\x02 repos",
            result.bio or "",
            result.html_url,
        )


commands = {
    "code": search_code,
    "issues": search_issues,
    "repo": search_repo,
    "user": search_user,
}

user_results: dict[str, dict[str, Generator[Result, None, None]]] = {}


@hook.command("ghn", "ghnext", autohelp=False)
def ghn_cmd(chan, nick):
    """Next result in the for GitHub search"""
    next_result_generator = user_results.get(chan, {}).get(nick)
    if not next_result_generator:
        return "You haven't searched for anything yet. Use gh <subcommand> <query> to search."
    try:
        result = next(next_result_generator)
    except StopIteration:
        return "No more results."
    return result.as_list()


@hook.command("gh", "github", autohelp=False)
def gh_cmd(text, event, reply, bot, nick, chan):

    arguments = shlex.split(text)
    if not arguments:
        return "Usage: gh <subcommand> [args] <query>"

    cmd = arguments[0]
    if cmd not in commands and cmd not in ("help", "-h", "--help"):
        return f"Unknown subcommand: {cmd}. Try using help to see available subcommands."

    if "help" in arguments or "-h" in arguments or "--help" in arguments:
        return "Possible subcommands: " + ", ".join(commands.keys())

    if cmd not in commands:
        return "Unknown subcommand: " + cmd

    func = commands[cmd]
    input_args = arguments[1:]
    query = " ".join(input_args)

    access_token = bot.config.get("api_keys", {}).get("github", None)
    if not access_token:
        return "No GitHub access token configured."

    auth = Auth.Token(access_token)
    g = Github(auth=auth)

    try:
        results = func(g, query)

        if chan not in user_results:
            user_results[chan] = {}

        user_results[chan][nick] = results
        return ghn_cmd(chan, nick)
    except GithubException as e:
        return f"Error: {e}"
