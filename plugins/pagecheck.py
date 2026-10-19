import urllib.parse
from typing import Annotated

import requests
import requests.exceptions

from cloudbot import hook
from cloudbot.hook import Arg
from cloudbot.util.http import parse_soup
from cloudbot.util.web import get_session


@hook.command("down", "offline", "up")
def down(url: Annotated[str, Arg("Site to check")]):
    """checks if <url> is online or offline"""

    if "://" not in url:
        url = "http://" + url

    url = "http://" + urllib.parse.urlparse(url).netloc

    try:
        r = get_session().get(url)
        r.raise_for_status()
    except requests.exceptions.ConnectionError:
        return f"{url} seems to be down"
    else:
        return f"{url} seems to be up"


@hook.command()
def isup(url: Annotated[str, Arg("Site to check")]):
    """uses isup.me to check if <url> is online or offline"""

    # slightly overcomplicated, esoteric URL parsing
    _, auth, path, _, _ = urllib.parse.urlsplit(url)

    domain = auth or path

    try:
        response = get_session().get("http://isup.me/" + domain)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        return "Failed to get status."
    if response.status_code != requests.codes.ok:
        return "Failed to get status."

    soup = parse_soup(response.text)

    content = soup.find("div", id="domain-main-content").text.strip()

    if "not just you" in content:
        return (
            f"It's not just you. {url} looks \x02\x034down\x02\x0f from here!"
        )

    if "is up" in content:
        return f"It's just you. {url} is \x02\x033up\x02\x0f."

    return "Huh? That doesn't look like a site on the interweb."
