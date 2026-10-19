import random
import re
import string
from collections import defaultdict
from typing import Annotated

from sqlalchemy import Column, String, Table, and_
from sqlalchemy.orm import Session

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import database
from cloudbot.util.pager import CommandPager, paginated_list

category_re = re.compile(r"[A-Za-z0-9]+")

# borrowed pagination code from grab.py
cat_pages: dict[str, dict[str, CommandPager]] = defaultdict(dict)
confirm_keys: dict[str, dict[str, str]] = defaultdict(dict)

table = Table(
    "profile",
    database.metadata,
    Column("chan", String),
    Column("nick", String),
    Column("category", String),
    Column("text", String),
)

profile_cache: dict[str, dict[str, dict[str, str]]] = {}


@hook.on_start()
def load_cache(db):
    new_cache = profile_cache.copy()
    new_cache.clear()
    for row in db.execute(table.select().order_by(table.c.category)):
        nick = row["nick"].lower()
        cat = row["category"]
        text = row["text"]
        chan = row["chan"]
        new_cache.setdefault(chan, {}).setdefault(nick, {})[cat] = text

    profile_cache.clear()
    profile_cache.update(new_cache)


def format_profile(nick, category, text):
    # Add zwsp to avoid pinging users
    nick = "{}{}{}".format(nick[0], "\u200b", nick[1:])
    msg = f"{nick}->{category}: {text}"
    return msg


# modified from grab.py
@hook.command("moreprofile", autohelp=False)
def moreprofile(
    chan: str,
    nick: str,
    event: CommandEvent,
    page: Annotated[str | None, Arg("Page number")] = None,
):
    """If a category search has lots of results the results are paginated. If the most recent search is
    paginated the pages are stored for retrieval. If no argument is given the next page will be returned else a page
    number can be specified."""
    chan_pages = cat_pages[chan.casefold()]
    pages = chan_pages.get(nick.casefold())
    if not pages:
        event.notice("There are no category pages to show.")
        return

    for line in pages.handle_lookup(page):
        event.notice(line)


@hook.command()
def profile(
    chan: str,
    event: CommandEvent,
    nick: str,
    target: Annotated[str, Arg("Nick whose profile to show", kind="user")],
    category: Annotated[str | None, Arg("Category to show")] = None,
):
    """Returns a user's saved profile data from \"<category>\", or lists all available profile
    categories for the user if no category specified"""
    chan_cf = chan.casefold()
    nick_cf = nick.casefold()

    # Check if we are in a PM with the user
    if nick_cf == chan_cf:
        return "Profile data not available outside of channels"

    chan_profiles = profile_cache.get(chan_cf, {})

    pnick = target
    pnick_cf = pnick.casefold()
    user_profile = chan_profiles.get(pnick_cf, {})
    if not user_profile:
        event.notice(f"User {pnick} has no profile data saved in this channel")
        return None

    # Check if the caller specified a profile category, if not, send a NOTICE with the users registered categories
    if not category:
        cats = list(user_profile.keys())

        pager = paginated_list(cats, ", ", pager_cls=CommandPager)
        cat_pages[chan_cf][nick_cf] = pager
        page = pager.next()
        page[0] = f"Categories: {page[0]}"
        if len(pager) > 1:
            page[-1] += " .moreprofile"

        for line in page:
            event.notice(line)

        return None

    cat_cf = category.casefold()
    if cat_cf not in user_profile:
        event.notice(
            f"User {pnick} has no profile data for category {category} in this channel"
        )
        return None

    content = user_profile[cat_cf]
    return format_profile(pnick, category, content)


@hook.command()
def profileadd(
    chan: str,
    nick: str,
    event: CommandEvent,
    db: Session,
    category: Annotated[str, Arg("Category to set")],
    content: Annotated[str, Arg("Text to store")],
):
    """Adds data to your profile in the current channel under \"<category>\" """
    if nick.casefold() == chan.casefold():
        return "Profile data can not be set outside of channels"

    if not category_re.fullmatch(category):
        event.notice("Invalid data")
        return None

    chan_profiles = profile_cache.get(chan.casefold(), {})
    user_profile = chan_profiles.get(nick.casefold(), {})
    if category.casefold() not in user_profile:
        db.execute(
            table.insert().values(
                chan=chan.casefold(),
                nick=nick.casefold(),
                category=category.casefold(),
                text=content,
            )
        )
        db.commit()
        load_cache(db)
        return f"Created new profile category {category}"

    db.execute(
        table.update()
        .values(text=content)
        .where(
            and_(
                table.c.nick == nick.casefold(),
                table.c.chan == chan.casefold(),
                table.c.category == category.casefold(),
            )
        )
    )
    db.commit()
    load_cache(db)
    return f"Updated profile category {category}"


@hook.command()
def profiledel(
    nick: str,
    chan: str,
    event: CommandEvent,
    db: Session,
    category: Annotated[str, Arg("Category to delete")],
):
    """Deletes \"<category>\" from a user's profile"""
    if nick.casefold() == chan.casefold():
        return "Profile data can not be set outside of channels"

    chan_profiles = profile_cache.get(chan.casefold(), {})
    user_profile = chan_profiles.get(nick.casefold(), {})
    if category.casefold() not in user_profile:
        event.notice("That category does not exist in your profile")
        return None

    db.execute(
        table.delete().where(
            and_(
                table.c.nick == nick.casefold(),
                table.c.chan == chan.casefold(),
                table.c.category == category.casefold(),
            )
        )
    )
    db.commit()
    load_cache(db)
    return f"Deleted profile category {category}"


@hook.command(autohelp=False)
def profileclear(
    nick: str,
    chan: str,
    event: CommandEvent,
    db: Session,
    key: Annotated[str | None, Arg("Confirmation key", rest=False)] = None,
):
    """Clears all of your profile data in the current channel"""
    if nick.casefold() == chan.casefold():
        return "Profile data can not be set outside of channels"

    if key:
        if (
            nick in confirm_keys[chan.casefold()]
            and key == confirm_keys[chan.casefold()][nick.casefold()]
        ):
            del confirm_keys[chan.casefold()][nick.casefold()]
            db.execute(
                table.delete().where(
                    and_(
                        table.c.nick == nick.casefold(),
                        table.c.chan == chan.casefold(),
                    )
                )
            )
            db.commit()
            load_cache(db)
            return f"Profile data cleared for {nick}."

        event.notice("Invalid confirm key")
        return None

    key = "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(10)
    )
    confirm_keys[chan.casefold()][nick.casefold()] = key
    event.notice(
        f'Are you sure you want to clear all of your profile data in {chan}? use ".profileclear {key}" to confirm'
    )
    return None
