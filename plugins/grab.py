import logging
import random
from collections import defaultdict
from threading import RLock
from typing import Annotated

from sqlalchemy import Column, String, Table
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cloudbot import hook
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import database
from cloudbot.util.pager import CommandPager, paginated_list

search_pages: dict[str, dict[str, CommandPager]] = defaultdict(dict)

table = Table(
    "grab",
    database.metadata,
    Column("name", String),
    Column("time", String),
    Column("quote", String),
    Column("chan", String),
)

grab_cache: dict[str, dict[str, list[str]]] = {}
grab_locks: dict[str, dict[str, RLock]] = defaultdict(dict)
grab_locks_lock = RLock()
cache_lock = RLock()

logger = logging.getLogger("cloudbot")


@hook.on_start()
def load_cache(db):
    new_cache = grab_cache.copy()
    new_cache.clear()
    for row in db.execute(table.select().order_by(table.c.time)):
        name = row["name"].lower()
        quote = row["quote"]
        chan = row["chan"]
        new_cache.setdefault(chan, {}).setdefault(name, []).append(quote)

    with cache_lock:
        grab_cache.clear()
        grab_cache.update(new_cache)


@hook.command("moregrab", autohelp=False)
def moregrab(
    chan: str,
    conn: IrcClient,
    page: Annotated[str | None, Arg("Page number")] = None,
):
    """if a grab search has lots of results the results are pagintated. If the most recent search is paginated
    the pages are stored for retreival. If no argument is given the next page will be returned else a page number can
    be specified."""
    pages = search_pages[conn.name].get(chan)
    if not pages:
        return "There are no grabsearch pages to show."

    return pages.handle_lookup(page)


def check_grabs(name, quote, chan):
    try:
        if quote in grab_cache[chan][name]:
            return True

        return False
    except KeyError:
        return False


def grab_add(nick, time, msg, chan, db):
    # Adds a quote to the grab table
    db.execute(
        table.insert().values(name=nick, time=time, quote=msg, chan=chan)
    )
    db.commit()
    load_cache(db)


def get_latest_line(conn, chan, nick):
    history = conn.history.get(chan, [])
    for name, timestamp, msg in reversed(history):
        if nick.casefold() == name.casefold():
            return name, timestamp, msg

    return None, None, None


@hook.command()
def grab(
    target: Annotated[str, Arg("Nick to grab", kind="user")],
    nick: str,
    chan: str,
    db: Session,
    conn: IrcClient,
):
    """grabs the last message from the specified nick and adds it to the quote database"""
    if target.lower() == nick.lower():
        return "Didn't your mother teach you not to grab yourself?"

    with grab_locks_lock:
        grab_lock = grab_locks[conn.name.casefold()].setdefault(
            chan.casefold(), RLock()
        )

    with grab_lock:
        name, timestamp, msg = get_latest_line(conn, chan, target)
        if not msg:
            return f"I couldn't find anything from {target} in recent history."

        if check_grabs(target.casefold(), msg, chan):
            return f"I already have that quote from {target} in the database"

        try:
            grab_add(name.casefold(), timestamp, msg, chan, db)
        except SQLAlchemyError:
            logger.exception(
                "Error occurred when grabbing %s in %s", name, chan
            )
            return "Error occurred."

        if check_grabs(name.casefold(), msg, chan):
            return "the operation succeeded."

        return "the operation failed"


def format_grab(name, quote):
    # add nonbreaking space to nicks to avoid highlighting people with printed grabs
    name = "{}{}{}".format(name[0], "\u200b", name[1:])
    if quote.startswith("\x01ACTION") or quote.startswith("*"):
        quote = quote.replace("\x01ACTION", "").replace("\x01", "")
        out = f"* {name}{quote}"
        return out

    out = f"<{name}> {quote}"
    return out


@hook.command("lastgrab", "lgrab")
def lastgrab(
    target: Annotated[str, Arg("Nick to show", kind="user")],
    chan: str,
    event: CommandEvent,
):
    """prints the last grabbed quote from <target>."""
    try:
        with cache_lock:
            lgrab = grab_cache[chan][target.lower()][-1]
    except (KeyError, IndexError):
        return f"{target} has never been grabbed."

    if lgrab:
        event.message(format_grab(target, lgrab), chan)

    return None


def random_grab(chan: str, nicks: str | None = None) -> str | None:
    """Pick a random grab in the channel, from the given nicks when there are any."""
    with cache_lock:
        chan_grabs = grab_cache.get(chan)
        if not chan_grabs:
            return None

        if nicks:
            matching_quotes = [
                (nick, quote)
                for nick in nicks.split()
                for quote in chan_grabs.get(nick.lower(), [])
            ]
        else:
            matching_quotes = [
                (name, quote)
                for name, quotes in chan_grabs.items()
                for quote in quotes
            ]

    if not matching_quotes:
        return None

    name, quote_text = random.choice(matching_quotes)
    return format_grab(name, quote_text)


@hook.command("grabrandom", "grabr", autohelp=False)
def grabrandom(
    chan: str,
    event: CommandEvent,
    nicks: Annotated[
        str | None,
        Arg("Nicks to pick a quote from", kind="user", rest=True),
    ] = None,
):
    """grabs a random quote from the grab database"""
    quote = random_grab(chan, nicks)
    if quote is None:
        return f"I couldn't find any grabs in {chan}."

    event.message(quote)
    return None


@hook.command("grabsearch", "grabs", autohelp=False)
def grabsearch(
    chan: str,
    conn: IrcClient,
    query: Annotated[str | None, Arg("Nick or text to match")] = None,
):
    """matches "text" against nicks or grab strings in the database"""
    text = query or ""
    result: list[tuple[str, str]] = []
    lower_text = text.lower()
    with cache_lock:
        try:
            chan_grabs = grab_cache[chan]
        except LookupError:
            return f"I couldn't find any grabs in {chan}."

        try:
            quotes = chan_grabs[lower_text]
        except KeyError:
            pass
        else:
            result.extend((text, quote) for quote in quotes)

        for name, quotes in chan_grabs.items():
            if name != lower_text:
                result.extend(
                    (name, quote)
                    for quote in quotes
                    if lower_text in quote.lower()
                )

    if not result:
        return f"I couldn't find any matches for {text}."

    grabs = []
    for name, quote in result:
        if lower_text == name:
            name = text

        grabs.append(format_grab(name, quote))

    pager = paginated_list(grabs, pager_cls=CommandPager)
    search_pages[conn.name][chan] = pager
    page = pager.next()
    if len(pager) > 1:
        page[-1] += " .moregrab"

    return page
