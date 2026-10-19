from typing import Annotated
from urllib.parse import urlparse

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import web


@hook.command()
def shorten(
    url: Annotated[str, Arg("URL to shorten")],
    event: CommandEvent,
    custom: Annotated[str | None, Arg("Custom shortlink", rest=False)] = None,
):
    """shortens a url with [custom] as an optional custom shortlink"""
    try:
        return web.shorten(url, custom=custom)
    except web.ServiceError as e:
        event.reply(str(e))
        raise


@hook.command()
def expand(url: Annotated[str, Arg("URL to unshorten")], event: CommandEvent):
    """unshortens <url>"""
    try:
        return web.expand(url)
    except web.ServiceError as e:
        event.reply(str(e))
        raise


@hook.command()
def isgd(
    url: Annotated[str, Arg("URL to shorten")],
    event: CommandEvent,
    custom: Annotated[str | None, Arg("Custom shortlink", rest=False)] = None,
):
    """shortens a url using is.gd with [custom] as an optional custom shortlink,
    or unshortens <url> if already short"""
    try:
        if urlparse(url).hostname == "is.gd":
            return web.expand(url, "is.gd")

        return web.shorten(url, custom=custom, service="is.gd")
    except web.ServiceError as e:
        event.reply(str(e))
        raise


@hook.command()
def googl(
    url: Annotated[str, Arg("URL to shorten")],
    event: CommandEvent,
    custom: Annotated[str | None, Arg("Custom shortlink", rest=False)] = None,
):
    """shorten <url> using goo.gl with [custom] as an option custom shortlink,
    or unshortens <url> if already short"""
    try:
        if urlparse(url).hostname == "goo.gl":
            return web.expand(url, "goo.gl")

        return web.shorten(url, custom=custom, service="goo.gl")
    except web.ServiceError as e:
        event.reply(str(e))
        raise


@hook.command()
def gitio(
    url: Annotated[str, Arg("URL to shorten")],
    event: CommandEvent,
    custom: Annotated[str | None, Arg("Custom shortlink", rest=False)] = None,
):
    """shortens a github URL <url> using git.io with [custom] as an optional custom shortlink,
    or unshortens <url> if already short"""
    try:
        if urlparse(url).hostname == "git.io":
            return web.expand(url, "git.io")

        return web.shorten(url, custom=custom, service="git.io")
    except web.ServiceError as e:
        event.reply(str(e))
        raise
