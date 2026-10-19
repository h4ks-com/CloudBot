from typing import Annotated
from urllib.error import HTTPError, URLError

from cloudbot import hook
from cloudbot.hook import Arg
from cloudbot.util import http

formats = {
    "taken": "\x034{domain}\x0f{path}",
    "available": "\x033{domain}\x0f{path}",
    "other": "\x031{domain}\x0f{path}",
}


def format_domain(domain):
    if domain["availability"] in formats:
        domainformat = formats[domain["availability"]]
    else:
        domainformat = formats["other"]
    return domainformat.format(**domain)


@hook.command("domain", "domainr")
def domainr(domain: Annotated[str, Arg("Domain to look up")]):
    """uses domain.nr's API to search for a domain, and similar domains"""
    try:
        data = http.get_json("http://domai.nr/api/json/search?q=" + domain)
    except (URLError, HTTPError):
        return "Unable to get data for some reason. Try again later."
    if data["query"] == "":
        return "An error occurred: {status} - {message}".format(**data["error"])

    domains = [format_domain(domain) for domain in data["results"]]
    return "Domains: {}".format(", ".join(domains))
