# Searches for linux and library packages on various repositories
# Author: Matheus Fillipe
# Date: 09/07/202


import re
from collections.abc import Callable, Generator
from dataclasses import InitVar, dataclass, field
from datetime import datetime
from urllib.parse import quote

from bs4 import BeautifulSoup
from bs4.element import Tag
from curl_cffi import requests
from thefuzz import fuzz

from cloudbot import hook
from cloudbot.util.web import get_session


def _tag_text(tag: Tag | None) -> str:
    """Return stripped text of an optional bs4 Tag, '' if None."""
    if tag is None:
        return ""
    return tag.text.strip()


def _tag_attr(tag: Tag | None, attr: str) -> str:
    """Return single string attribute of an optional bs4 Tag, '' if None."""
    if tag is None:
        return ""
    value = tag.get(attr)
    if value is None:
        return ""
    if isinstance(value, list):
        return value[0].strip() if value else ""
    return value.strip()


class Config:
    """Configuration class."""

    api_url: str = "https://pypi.org/search/"
    page_size: int = 2
    sort_by: str = "name"
    date_format: str = "%d-%-m-%Y"
    link_defualt_format: str = "https://pypi.org/project/{package.name}"


config = Config()
MAX_RESULTS = 30


@dataclass
class Package:
    """Package class."""

    name: str
    version: str
    updated: str
    description: str
    link_init: InitVar[str | None] = None
    link: str = field(init=False)
    released_date: datetime | str | None = field(init=False, default=None)

    def __post_init__(self, link_init: str | None = None) -> None:
        self.link = link_init or config.link_defualt_format.format(package=self)
        if self.updated:
            for strfmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"]:
                try:
                    self.released_date = datetime.strptime(self.updated, strfmt)
                    break
                except ValueError:
                    continue
            else:
                self.released_date = self.updated

    def released_date_str(self, date_format: str = config.date_format) -> str:
        """Return the released date as a string formatted according to
        date_formate ou Config.date_format (default)

        Returns:
            str: Formatted date string
        """
        if isinstance(self.released_date, datetime):
            return self.released_date.strftime(date_format)
        return self.released_date or ""

    def __str__(self) -> str:
        return f"\x02{self.name}\x02 {self.link} - {self.version} - {self.description} - {self.released_date or self.updated or ''}"


def pypi_search(query: str) -> Generator[Package, None, None]:
    """Search for packages matching the query.

    Yields:
        Package: package object
    """
    headers = {
        "Accept": "application/vnd.pypi.simple.v1+json",
    }

    response = get_session().get("https://pypi.org/simple/", headers=headers)
    response.raise_for_status()
    data = response.json()
    matches = [
        p["name"]
        for p in data["projects"]
        if query.casefold() in p["name"].casefold()
    ]
    if not matches:
        return
    # Sort by proximity to query
    matches.sort(key=lambda x: fuzz.ratio(query, x), reverse=True)
    matches = matches[:MAX_RESULTS]
    for name in matches:
        response = get_session().get(
            f"https://pypi.org/pypi/{name}/json", headers=headers
        )
        if response.status_code != 200:
            yield Package(name, "-!Failed to get info!-", "", "", "")
            continue
        data = response.json()
        info = data["info"]
        package = info["name"]
        version = info["version"]
        link = info["package_url"]
        releases = data["releases"]
        if version in releases and len(releases[version]) > 0:
            released = releases[version][0]["upload_time"]
        else:
            version_ = next(iter(releases), None)
            if version_ is None:
                released = ""
            elif len(releases[version_]) > 0:
                released = releases[version_][0]["upload_time"]
                version = version_
            else:
                released = ""
        description = info["summary"]
        yield Package(package, version, released, description, link)


def aur_search(query: str) -> Generator[Package, None, None]:
    url = "https://aur.archlinux.org/packages"
    response = requests.get(url, params={"K": query})
    if response.status_code != 200:
        return
    soup = BeautifulSoup(response.text, "html.parser")
    try:
        for package in soup.select("tbody tr"):
            columns = package.select("td")
            name = _tag_text(columns[0].select_one("a"))
            link = "https://aur.archlinux.org" + _tag_attr(
                columns[0].select_one("a"), "href"
            )
            version = columns[1].text.strip()
            released = ""
            description = columns[4].text.strip()
            yield Package(name, version, released, description, link)
    except IndexError:
        return


def arch_search(query: str) -> Generator[Package, None, None]:
    url = "https://archlinux.org/packages/"
    response = requests.get(url, params={"q": query})
    if response.status_code != 200:
        return
    soup = BeautifulSoup(response.text, "html.parser")
    try:
        for package in soup.select("tbody tr"):
            columns = package.select("td")
            name = _tag_text(columns[2].select_one("a"))
            link = "https://archlinux.org" + _tag_attr(
                columns[2].select_one("a"), "href"
            )
            version = columns[3].text.strip()
            released = ""
            description = columns[4].text.strip()
            yield Package(name, version, released, description, link)
    except IndexError:
        return


def crates_search(query: str) -> Generator[Package, None, None]:
    url = "https://crates.io/api/v1/crates"
    response = requests.get(
        url,
        params={"q": query, "per_page": 20, "page": 1},
        headers={
            "Accept": "application/json",
            "user-agent": "cloudbot-irc",
        },
    )
    if response.status_code != 200:
        return
    data = response.json()
    for package in data["crates"]:

        def safeget(key: str, _pkg=package) -> str:
            return (_pkg.get(key, "") or "").strip()

        name = safeget("name")
        link = f"https://crates.io/crates/{name}"
        version = safeget("newest_version")
        released = safeget("updated_at")
        description = safeget("description")
        yield Package(name, version, released, description, link)


def pubdev_search(query: str) -> Generator[Package, None, None]:
    url = "https://pub.dev/packages"
    response = requests.get(url, params={"q": query})
    if response.status_code != 200:
        return
    soup = BeautifulSoup(response.text, "html.parser")
    for package in soup.select("div.packages-item"):
        name = _tag_text(package.select_one("h3 a"))
        link = _tag_attr(package.select_one("h3 a"), "href")
        if link.startswith("/"):
            link = "https://pub.dev" + link
        version = _tag_text(package.select_one("span.packages-metadata-block"))

        # Remove release date from version
        version = re.sub(r"\(.+\)", "", version).strip()

        date = package.select_one("a.-x-ago")
        released = date.text.strip() if date else ""
        description_tag = package.select_one("div.packages-description")
        description = description_tag.text.strip() if description_tag else ""
        platforms: list[str] = []
        for badge in package.select("div.-pub-tag-badge") or []:
            badge_text = " ".join(
                [s.text for s in badge.select("a.tag-badge-sub")]
            )
            if badge_text:
                platforms.append(badge_text)
        if platforms:
            description += f" Platforms: {platforms}"

        yield Package(name, version, released, description, link)


def ubuntu_search(query: str) -> Generator[Package, None, None]:
    url = "https://packages.ubuntu.com/search"
    response = requests.get(
        url,
        params={
            "keywords": query,
            "searchon": "all",
            "suite": "all",
            "section": "all",
        },
    )
    if response.status_code != 200:
        return
    soup = BeautifulSoup(response.text, "html.parser")
    soup.select("h3")
    for name_tag, package in zip(soup.select("h3"), soup.select("h3+ul")):
        name = name_tag.text.strip().lstrip("Package ")
        ubuntus: list[str] = []
        link = ""
        li: Tag | None = None
        for li in package.select("li"):
            ver = _tag_text(li.select_one("a.resultlink"))
            link = "https://packages.ubuntu.com" + _tag_attr(
                li.select_one("a.resultlink"), "href"
            )
            ubuntus.append(ver)

        if li is None:
            continue
        rows = li.text.strip().split("\n")
        description = " ".join(rows[:3]).strip().replace("\t", " ")
        version = rows[3].strip()
        released = str(ubuntus)

        yield Package(name, version, released, description, link)


def search_npmjs(query: str) -> Generator[Package, None, None]:
    url = "https://www.npmjs.com/search"
    response = requests.get(
        url, params={"q": query}, headers={"x-spiferack": "1"}
    )
    if response.status_code != 200:
        return
    data = response.json()
    for package in data["objects"]:
        package = package["package"]

        def safeget(key: str | list, _pkg=package) -> str:
            if isinstance(key, str):
                return (_pkg.get(key, "") or "").strip()
            if isinstance(key, list):
                d = _pkg.get(key[0])
                for k in key[1:]:
                    d = d.get(k)
                    if d is None:
                        break
                return (str(d) or "").strip()
            raise ValueError(f"Unknown key type {type(key)}")

        name = safeget("name")
        link = safeget(["links", "npm"])
        version = safeget("version")
        released = package.get("date", {}).get("rel", "").strip()
        description = safeget("description")
        yield Package(name, version, released, description, link)


def search_nuget(query: str) -> Generator[Package, None, None]:
    query = quote(query)
    url = f"https://www.nuget.org/packages?q={query}&includeComputedFrameworks=true&prerel=true&sortby=relevance"
    response = requests.get(url)
    if response.status_code != 200:
        return
    soup = BeautifulSoup(response.text, "html.parser")
    results = soup.select("#results-column")
    if not results:
        return
    for package in results[0].select("li.package"):
        title = package.select_one("h2.package-title")
        if title is None:
            continue
        name = title.text.strip()
        link = "https://www.nuget.org" + _tag_attr(
            title.select_one("a"), "href"
        )
        package_list = package.select_one("ul.package-list")
        if package_list is None:
            continue
        version = (
            package_list.select("li")[3].text.strip().lstrip("Latest version ")
        )
        released = package_list.select("li")[2].text.strip()
        description = _tag_text(package.select_one("div.package-details"))
        yield Package(name, version, released, description, link)


def search_dockerhub(query: str) -> Generator[Package, None, None]:
    url = "https://hub.docker.com/api/search/v3/catalog/search"
    response = requests.get(
        url, params={"query": quote(query), "from": 0, "type": "image"}
    )
    if response.status_code != 200:
        return
    data = response.json()
    for package in data["results"]:
        desc = ""
        for plan in package.get("rate_plans", []):
            for ops in plan.get("operating_systems"):
                oses = list(ops.values())
                if oses and oses[0]:
                    desc = f"\x02OS:\x02 {', '.join(oses)}"
                    break
            for arch in plan.get("architectures"):
                archs = list(arch.values())
                if archs and archs[0]:
                    desc += f" - \x02Arch:\x02 {', '.join(arch.values())}"
                    break
            for repo in plan.get("repositories", []):
                pulls = repo.get("pull_count")
                if pulls:
                    desc += f" - ⬇️{pulls}"
                    break
            if desc:
                break

        name = package["name"]
        id = package["id"]
        link = f"https://hub.docker.com/r/{id}"
        version = package["short_description"]
        released = package["updated_at"]
        description = desc
        yield Package(name, version, released, description, link)


_REPOS = {
    ("aur", "yay", "picom"): aur_search,
    ("pypi", "pip", "python"): pypi_search,
    ("arch", "pacman"): arch_search,
    ("crates", "cargo", "rust"): crates_search,
    ("pubdev", "dart", "flutter", "pub"): pubdev_search,
    ("ubuntu", "apt"): ubuntu_search,
    ("npmjs", "npm", "yarn"): search_npmjs,
    ("nuget", "dotnet", ".net"): search_nuget,
    ("docker", "dockerhub"): search_dockerhub,
}

REPOS: dict[str, Callable[[str], Generator[Package, None, None]]] = {}

# Flatten keys
for k, v in _REPOS.items():
    if isinstance(k, tuple):
        for i in k:
            REPOS[i] = v


results_queue: dict[tuple[str, str], Generator[Package, None, None]] = {}


@hook.command("pkglist", autohelp=False)
def pkglist():
    """List all repos."""
    return ", ".join("/".join(k) for k in _REPOS.keys())


def pop3(results, reply):
    lines = []
    for _ in range(3):
        try:
            lines.append(str(next(results)))
        except StopIteration:
            break
    if not lines:
        return "No [more] results found."
    reply(*lines)
    return None


@hook.command("pkgn", autohelp=False)
def pkgn(text, bot, chan, nick, reply):
    """<nick> - Returns next search result for pkg command for nick or yours by default"""
    if (chan, nick) not in results_queue:
        return f"Nick '{nick}' has no queue."
    results = results_queue[(chan, nick)]
    user = text.strip().split()[0] if text.strip() else ""
    if user:
        if (chan, user) in results_queue:
            results = results_queue[(chan, user)]
        else:
            return f"Nick '{user}' has no queue."
    return pop3(results, reply)


@hook.command("pkg", autohelp=False)
def pkg(text, bot, chan, nick, reply):
    """<query> - Returns first search result for pkg command"""
    if not text:
        return "Please specify a repo and query."

    repo = text.strip().split()[0]
    if repo not in REPOS:
        return (
            f"Repo '{repo}' not found. Use .'pkglist' to see available repos."
        )

    results_queue[(chan, nick)] = REPOS[repo](
        " ".join(text.strip().split()[1:])
    )
    results = results_queue[(chan, nick)]
    if results is None or not results:
        return "No [more] results found."

    return pop3(results, reply)
