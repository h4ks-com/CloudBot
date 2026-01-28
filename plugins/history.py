import re
import time
from datetime import datetime

import requests
from sqlalchemy import (
    Column,
    Float,
    PrimaryKeyConstraint,
    String,
    Table,
    and_,
    delete,
    desc,
    select,
)

from cloudbot import hook
from cloudbot.event import EventType
from cloudbot.util import database, formatting, queue, timeformat
from cloudbot.util.http import parse_soup
from cloudbot.util.web import get_session

seen_table = Table(
    "seen_user",
    database.metadata,
    Column("name", String),
    Column("time", Float),
    Column("quote", String),
    Column("chan", String),
    Column("host", String),
    PrimaryKeyConstraint("name", "chan"),
)

# Constants for URL tracking
MAX_URLS_PER_USER = 100  # Maximum URLs to store per user
URLS_PER_PAGE = 3  # URLs to display per page

# Table for storing user URLs with titles
user_urls_table = Table(
    "user_urls",
    database.metadata,
    Column("network", String),
    Column("chan", String),
    Column("nick", String),
    Column("url", String),
    Column("title", String),
    Column("timestamp", Float),
    PrimaryKeyConstraint("network", "chan", "nick", "url", "timestamp"),
)

# Queue for pagination
url_results_queue = queue.Queue()


RE_URL = "\\b(?:https?:\\/\\/)?(?:www\\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\\.[a-zA-Z0-9()]{1,6}\\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&\\/=]*)\\b"

# Headers for URL title fetching
URL_HEADERS = {
    "Accept-Language": "en-US,en;q=0.5",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/53.0.2785.116 Safari/537.36",
}

MAX_RECV = 1000000


def get_url_title(url: str) -> str | None:
    """Fetches title of a URL using similar logic to link_announcer.py"""
    try:
        with get_session().get(
            url, headers=URL_HEADERS, stream=True, timeout=3
        ) as response:
            if not response.encoding or not response.ok:
                return None

            content = response.raw.read(MAX_RECV, decode_content=True)
            encoding = response.encoding
    except (
        requests.RequestException,
        requests.Timeout,
        requests.ConnectionError,
    ):
        # Expected: network issues, timeouts, invalid URLs
        return None

    try:
        html = parse_soup(content, from_encoding=encoding)
        if html.title and html.title.text:
            title = html.title.text.strip()
            if len(title) > 100:  # Truncate very long titles
                title = title[:100] + " ... [trunc]"
            return title
    except (ValueError, TypeError):
        # Expected: malformed HTML, encoding issues
        pass

    return None


def track_user_urls(event, db) -> None:
    """Track URLs posted by users for enhanced .urls command"""
    # Only track in channels, not private messages
    if event.chan[:1] != "#":
        return

    # Find URLs in message
    found_urls = re.findall(RE_URL, event.content)
    if not found_urls:
        return

    current_timestamp = time.time()

    for url in found_urls:
        # Ensure URL has protocol
        if not url.startswith(("http://", "https://")):
            url = "http://" + url

        # Get title for the URL
        url_title = get_url_title(url) or "No title available"

        # Insert the URL into database
        try:
            db.execute(
                user_urls_table.insert().values(
                    network=event.conn.name,
                    chan=event.chan.lower(),
                    nick=event.nick.lower(),
                    url=url,
                    title=url_title,
                    timestamp=current_timestamp,
                )
            )

            # Clean up old URLs to maintain limit
            cleanup_old_urls(
                db, event.conn.name, event.chan.lower(), event.nick.lower()
            )

        except Exception:
            # If duplicate URL (same timestamp), skip it
            pass


def cleanup_old_urls(db, network: str, chan: str, nick: str) -> None:
    """Remove oldest URLs to maintain MAX_URLS_PER_USER limit"""
    # Count URLs for this user
    user_url_count = db.execute(
        select([user_urls_table.c.url])
        .where(user_urls_table.c.network == network)
        .where(user_urls_table.c.chan == chan)
        .where(user_urls_table.c.nick == nick)
    ).fetchall()

    if len(user_url_count) > MAX_URLS_PER_USER:
        # Get timestamps to remove (oldest ones)
        urls_to_remove = len(user_url_count) - MAX_URLS_PER_USER
        old_url_timestamps = db.execute(
            select([user_urls_table.c.timestamp])
            .where(user_urls_table.c.network == network)
            .where(user_urls_table.c.chan == chan)
            .where(user_urls_table.c.nick == nick)
            .order_by(user_urls_table.c.timestamp.asc())
            .limit(urls_to_remove)
        ).fetchall()

        # Delete old URLs
        for timestamp_row in old_url_timestamps:
            db.execute(
                delete(user_urls_table)
                .where(user_urls_table.c.network == network)
                .where(user_urls_table.c.chan == chan)
                .where(user_urls_table.c.nick == nick)
                .where(user_urls_table.c.timestamp == timestamp_row[0])
            )


def track_seen(event, db):
    """Tracks messages for the .seen command
    :type event: cloudbot.event.Event
    :type db: sqlalchemy.orm.Session
    """
    # keep private messages private
    now = time.time()
    if event.chan[:1] == "#" and not re.findall(
        "^s/.*/.*/$", event.content.lower()
    ):
        res = db.execute(
            seen_table.update()
            .values(time=now, quote=event.content, host=str(event.mask))
            .where(seen_table.c.name == event.nick.lower())
            .where(seen_table.c.chan == event.chan)
        )
        if res.rowcount == 0:
            db.execute(
                seen_table.insert().values(
                    name=event.nick.lower(),
                    time=now,
                    quote=event.content,
                    chan=event.chan,
                    host=str(event.mask),
                )
            )

        db.commit()


@hook.event([EventType.message, EventType.action], singlethread=True)
def chat_tracker(event, db, conn):
    """
    :type db: sqlalchemy.orm.Session
    :type event: cloudbot.event.Event
    :type conn: cloudbot.client.Client
    """
    if event.type is EventType.action:
        event.content = f"\x01ACTION {event.content}\x01"

    track_seen(event, db)
    track_user_urls(event, db)
    db.commit()


@hook.command()
def seen(text, nick, chan, db, event, is_nick_valid):
    """<nick> <channel> - tells when a nickname was last in active in one of my channels
    :type db: sqlalchemy.orm.Session
    :type event: cloudbot.event.Event
    """

    if event.conn.nick.lower() == text.lower():
        return "You need to get your eyes checked."

    if text.lower() == nick.lower():
        return "Have you looked in a mirror lately?"

    if not is_nick_valid(text):
        return "I can't look up that name, its impossible to use!"

    last_seen = db.execute(
        select([seen_table.c.name, seen_table.c.time, seen_table.c.quote])
        .where(seen_table.c.name == text.lower())
        .where(seen_table.c.chan == chan)
    ).fetchone()

    if last_seen:
        reltime = timeformat.time_since(last_seen[1])
        if last_seen[2][0:1] == "\x01":
            return f"{text} was last seen {reltime} ago: * {text} {last_seen[2][8:-1]}"
        else:
            return f"{text} was last seen {reltime} ago saying: {last_seen[2]}"
    else:
        return f"I've never seen {text} talking in this channel."


@hook.command("lastlink", "ll", "lasturl", autohelp=False)
def lastlink(text: str, chan: str, conn, db) -> str:
    """[<nick>] - gets the last link posted by a user or in the channel if no argument is supplied"""
    search_text = text.strip()
    target_nick = search_text.lower() if search_text else None

    # Query database for most recent URL
    last_url_query = select(
        [
            user_urls_table.c.url,
            user_urls_table.c.title,
            user_urls_table.c.timestamp,
            user_urls_table.c.nick,
        ]
    )
    last_url_query = last_url_query.where(
        user_urls_table.c.network == conn.name
    )
    last_url_query = last_url_query.where(
        user_urls_table.c.chan == chan.lower()
    )

    if search_text:
        # User-specific last link
        last_url_query = last_url_query.where(
            user_urls_table.c.nick == target_nick
        )

    last_url_query = last_url_query.order_by(
        desc(user_urls_table.c.timestamp)
    ).limit(1)

    url_result = db.execute(last_url_query).fetchone()

    if not url_result:
        return (
            "No links found"
            if not search_text
            else f"No links found for nick: {search_text}"
        )

    url, title, timestamp, posting_nick = url_result
    url_title = title or "No title available"

    # Format timestamp
    formatted_date = datetime.fromtimestamp(timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Format output with title
    if search_text:
        return f"{formatted_date} {search_text}: {url_title} - {url}"
    else:
        return f"{formatted_date} {posting_nick}: {url_title} - {url}"


@hook.command("userlinks", "urls", autohelp=False)
def userlinks(text: str, chan: str, conn, db, nick: str) -> str:
    """[<nick>] - gets recent links posted by a user or in the channel if no argument is supplied"""
    search_text = text.strip()
    target_nick = search_text.lower() if search_text else nick.lower()

    # Query database for URLs
    url_query = select(
        [
            user_urls_table.c.url,
            user_urls_table.c.title,
            user_urls_table.c.timestamp,
        ]
    )
    url_query = url_query.where(user_urls_table.c.network == conn.name)
    url_query = url_query.where(user_urls_table.c.chan == chan.lower())

    if search_text:
        # User-specific URLs
        url_query = url_query.where(user_urls_table.c.nick == target_nick)

    url_query = url_query.order_by(desc(user_urls_table.c.timestamp)).limit(
        URLS_PER_PAGE
    )

    page_results = db.execute(url_query).fetchall()

    if not page_results:
        return (
            "No links found"
            if not search_text
            else f"No links found for nick: {search_text}"
        )

    # Get all results for pagination
    if search_text:
        # Get total count for user
        total_query = select([user_urls_table.c.url]).where(
            and_(
                user_urls_table.c.network == conn.name,
                user_urls_table.c.chan == chan.lower(),
                user_urls_table.c.nick == target_nick,
            )
        )
    else:
        # Get total count for channel
        total_query = select([user_urls_table.c.url]).where(
            and_(
                user_urls_table.c.network == conn.name,
                user_urls_table.c.chan == chan.lower(),
            )
        )

    all_url_results = db.execute(
        total_query.order_by(desc(user_urls_table.c.timestamp))
    ).fetchall()

    # Store all results in queue for pagination
    queue_key = nick if not search_text else search_text
    url_results_queue[chan][queue_key] = all_url_results

    # Format output as 3 lines with titles and URLs
    formatted_lines = []
    for i, (url, title, timestamp) in enumerate(
        page_results[:URLS_PER_PAGE], 1
    ):
        url_title = title or "No title available"
        # Truncate long titles
        if len(url_title) > 50:
            url_title = formatting.truncate(url_title, 47) + "..."
        formatted_lines.append(f"{i}. {url_title} - {url}")

    output_prefix = (
        f"Recent links for {search_text}: "
        if search_text
        else "Recent links in channel: "
    )

    if len(all_url_results) > URLS_PER_PAGE:
        output_prefix += f"(showing {URLS_PER_PAGE} of {len(all_url_results)}, use .urlsn for more)"

    return output_prefix + "\n" + "\n".join(formatted_lines)


@hook.command("urlsn", autohelp=False)
def urls_next(text: str, chan: str, conn, nick: str) -> str:
    """[<nick>] - gets next page of links for a user or channel"""
    search_text = text.strip()
    queue_key = nick if not search_text else search_text

    try:
        queued_results = url_results_queue[chan][queue_key]
        if not queued_results:
            return "No [more] results found."
    except KeyError:
        return "No results found. Use .urls first."

    # Remove already shown URLs (first URLS_PER_PAGE)
    if len(queued_results) <= URLS_PER_PAGE:
        # Clear queue if no more results
        url_results_queue[chan][queue_key] = []
        return "No more results found."

    # Remove first page
    remaining_results = queued_results[URLS_PER_PAGE:]
    url_results_queue[chan][queue_key] = remaining_results

    # Get next page
    next_page_results = remaining_results[:URLS_PER_PAGE]

    # Format output
    output_lines = []
    start_number = (len(queued_results) - len(remaining_results)) + 1
    for i, (url, title, timestamp) in enumerate(
        next_page_results, start_number
    ):
        url_title = title or "No title available"
        if len(url_title) > 50:
            url_title = formatting.truncate(url_title, 47) + "..."
        output_lines.append(f"{i}. {url_title} - {url}")

    more_links_prefix = (
        f"More links for {search_text}: "
        if search_text
        else "More links in channel: "
    )

    if len(remaining_results) > URLS_PER_PAGE:
        more_links_prefix += f"(showing {URLS_PER_PAGE} more, {len(remaining_results) - URLS_PER_PAGE} remaining)"

    return more_links_prefix + "\n" + "\n".join(output_lines)


@hook.command("said", autohelp=False)
def searchword(text, chan, conn):
    """[<nick>] <text> - gets the last message sen't by the nick that contains the [text] string"""
    try:
        history = reversed(conn.history[chan])
    except KeyError:
        return "There is no history for this channel."

    text = text.strip()
    if not text or len(text.split()) < 2:
        return "Please provide a nick and a search string."

    search_nick = text.split()[0]
    text = text[len(search_nick) :].strip()

    i = 0
    max_i = 50000

    history.__next__()
    for nick, message_time, message in history:
        if i > max_i:
            break
        i += 1
        if nick == search_nick or not text or search_nick == "*":
            if text in message:
                date = datetime.fromtimestamp(message_time).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                message = message.replace("\x01ACTION ", "* ").replace(
                    "\x01", ""
                )
                message = message.replace(text, f"\x02{text}\x02")
                return f"{date} {nick}: {message}"

    return f"Seems like {search_nick} hasn't said anything containing '{text}' recently"


@hook.command("now", autohelp=False)
def now(text, chan, conn):
    """Returns now in local time"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@hook.command("utc", autohelp=False)
def utc(text, chan, conn):
    """Returns now in UTC"""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
