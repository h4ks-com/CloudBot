import asyncio
import re
import time
from collections import deque
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
from cloudbot.util import database, formatting, timeformat, queue
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
URLS_PER_PAGE = 3       # URLs to display per page

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


def get_url_title(url):
    """Fetch the title of a URL using similar logic to link_announcer.py"""
    try:
        with get_session().get(url, headers=URL_HEADERS, stream=True, timeout=3) as r:
            if not r.encoding or not r.ok:
                return None

            content = r.raw.read(MAX_RECV, decode_content=True)
            encoding = r.encoding
    except Exception:
        return None

    try:
        html = parse_soup(content, from_encoding=encoding)
        if html.title and html.title.text:
            title = html.title.text.strip()
            if len(title) > 100:  # Truncate very long titles
                title = title[:100] + " ... [trunc]"
            return title
    except Exception:
        pass

    return None


def track_user_urls(event, db):
    """Track URLs posted by users for the enhanced .urls command
    :type event: cloudbot.event.Event
    :type db: sqlalchemy.orm.Session
    """
    # Only track in channels, not private messages
    if event.chan[:1] != "#":
        return

    # Find URLs in the message
    urls = re.findall(RE_URL, event.content)
    if not urls:
        return

    now = time.time()
    
    for url in urls:
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
        
        # Get title for the URL
        title = get_url_title(url) or "No title available"
        
        # Insert the URL into database
        try:
            db.execute(
                user_urls_table.insert().values(
                    network=event.conn.name,
                    chan=event.chan.lower(),
                    nick=event.nick.lower(),
                    url=url,
                    title=title,
                    timestamp=now,
                )
            )
            
            # Clean up old URLs to maintain limit
            cleanup_old_urls(db, event.conn.name, event.chan.lower(), event.nick.lower())
            
        except Exception:
            # If duplicate URL (same timestamp), skip it
            pass


def cleanup_old_urls(db, network, chan, nick):
    """Remove oldest URLs to maintain MAX_URLS_PER_USER limit"""
    # Count URLs for this user
    count = db.execute(
        select([user_urls_table.c.url])
        .where(user_urls_table.c.network == network)
        .where(user_urls_table.c.chan == chan)
        .where(user_urls_table.c.nick == nick)
    ).fetchall()
    
    if len(count) > MAX_URLS_PER_USER:
        # Get the timestamps to remove (oldest ones)
        to_remove = len(count) - MAX_URLS_PER_USER
        old_urls = db.execute(
            select([user_urls_table.c.timestamp])
            .where(user_urls_table.c.network == network)
            .where(user_urls_table.c.chan == chan)
            .where(user_urls_table.c.nick == nick)
            .order_by(user_urls_table.c.timestamp.asc())
            .limit(to_remove)
        ).fetchall()
        
        # Delete the old URLs
        for row in old_urls:
            db.execute(
                delete(user_urls_table)
                .where(user_urls_table.c.network == network)
                .where(user_urls_table.c.chan == chan)
                .where(user_urls_table.c.nick == nick)
                .where(user_urls_table.c.timestamp == row[0])
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
def lastlink(text, chan, conn, db):
    """[<nick>] - gets the last link posted by a user or in the channel if no argument is supplied"""
    text = text.strip()
    target_nick = text.lower() if text else None
    
    # Query database for the most recent URL
    query = select([user_urls_table.c.url, user_urls_table.c.title, user_urls_table.c.timestamp, user_urls_table.c.nick])
    query = query.where(user_urls_table.c.network == conn.name)
    query = query.where(user_urls_table.c.chan == chan.lower())
    
    if text:
        # User-specific last link
        query = query.where(user_urls_table.c.nick == target_nick)
    
    query = query.order_by(desc(user_urls_table.c.timestamp)).limit(1)
    
    result = db.execute(query).fetchone()
    
    if not result:
        return "No links found" if not text else f"No links found for nick: {text}"
    
    url, title, timestamp, nick = result
    title = title or "No title available"
    
    # Format timestamp
    date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    
    # Format output with title
    if text:
        return f"{date} {text}: {title} - {url}"
    else:
        return f"{date} {nick}: {title} - {url}"


@hook.command("userlinks", "urls", autohelp=False)
def userlinks(text, chan, conn, db, nick):
    """[<nick>] - gets recent links posted by a user or in the channel if no argument is supplied"""
    text = text.strip()
    target_nick = text.lower() if text else nick.lower()
    
    # Query database for URLs
    query = select([user_urls_table.c.url, user_urls_table.c.title, user_urls_table.c.timestamp])
    query = query.where(user_urls_table.c.network == conn.name)
    query = query.where(user_urls_table.c.chan == chan.lower())
    
    if text:
        # User-specific URLs
        query = query.where(user_urls_table.c.nick == target_nick)
    else:
        # Channel-wide URLs - get from all users
        pass
    
    query = query.order_by(desc(user_urls_table.c.timestamp)).limit(URLS_PER_PAGE)
    
    results = db.execute(query).fetchall()
    
    if not results:
        return (
            "No links found" if not text else f"No links found for nick: {text}"
        )
    
    # Store remaining results in queue for pagination
    if text:
        # Get total count for user
        total_query = select([user_urls_table.c.url]).where(
            and_(
                user_urls_table.c.network == conn.name,
                user_urls_table.c.chan == chan.lower(),
                user_urls_table.c.nick == target_nick
            )
        )
    else:
        # Get total count for channel
        total_query = select([user_urls_table.c.url]).where(
            and_(
                user_urls_table.c.network == conn.name,
                user_urls_table.c.chan == chan.lower()
            )
        )
    
    all_results = db.execute(total_query.order_by(desc(user_urls_table.c.timestamp))).fetchall()
    
    # Store all results in queue for pagination
    url_results_queue[chan][nick if not text else text] = all_results
    
    # Format output as 3 lines with titles and URLs
    lines = []
    for i, (url, title, timestamp) in enumerate(results[:URLS_PER_PAGE], 1):
        title = title or "No title available"
        # Truncate long titles
        if len(title) > 50:
            title = formatting.truncate(title, 47) + "..."
        lines.append(f"{i}. {title} - {url}")
    
    prefix = f"Recent links for {text}: " if text else "Recent links in channel: "
    
    if len(all_results) > URLS_PER_PAGE:
        prefix += f"(showing {URLS_PER_PAGE} of {len(all_results)}, use .urlsn for more)"
    
    return prefix + "\n" + "\n".join(lines)


@hook.command("urlsn", autohelp=False)
def urls_next(text, chan, conn, nick):
    """[<nick>] - gets the next page of links for a user or channel"""
    text = text.strip()
    queue_key = nick if not text else text
    
    try:
        results = url_results_queue[chan][queue_key]
        if not results:
            return "No [more] results found."
    except KeyError:
        return "No results found. Use .urls first."
    
    # Remove already shown URLs (first URLS_PER_PAGE)
    if len(results) <= URLS_PER_PAGE:
        # Clear queue if no more results
        url_results_queue[chan][queue_key] = []
        return "No more results found."
    
    # Remove first page
    remaining = results[URLS_PER_PAGE:]
    url_results_queue[chan][queue_key] = remaining
    
    # Get next page
    next_page = remaining[:URLS_PER_PAGE]
    
    # Format output
    lines = []
    start_num = (len(results) - len(remaining)) + 1
    for i, (url, title, timestamp) in enumerate(next_page, start_num):
        title = title or "No title available"
        if len(title) > 50:
            title = formatting.truncate(title, 47) + "..."
        lines.append(f"{i}. {title} - {url}")
    
    prefix = f"More links for {text}: " if text else "More links in channel: "
    
    if len(remaining) > URLS_PER_PAGE:
        prefix += f"(showing {URLS_PER_PAGE} more, {len(remaining) - URLS_PER_PAGE} remaining)"
    
    return prefix + "\n" + "\n".join(lines)


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
