from requests import HTTPError

from cloudbot import hook
from cloudbot.util import formatting, web
from cloudbot.util.web import get_session
from plugins.ratelimit import Limit, check, record

base_url = "https://www.googleapis.com/books/v1/"
book_search_api = base_url + "volumes?"

BOOKS_BUCKET = "books"
BOOKS_MAX_RPD = 800
BOOKS_LIMITS = [
    Limit(86400, BOOKS_MAX_RPD, f"Daily Books cap reached ({BOOKS_MAX_RPD}). Resets in 24h."),
]


@hook.command("books", "gbooks")
def books(text, reply, bot, db):
    """<query> - Searches Google Books for <query>."""
    api_key = bot.config.get_api_key("google")
    if not api_key:
        return "This command requires a Google API key."

    cap_msg = check(db, BOOKS_BUCKET, BOOKS_LIMITS)
    if cap_msg:
        return cap_msg

    with get_session().get(
        book_search_api, params={"q": text, "key": api_key, "country": "US"}
    ) as response:
        try:
            response.raise_for_status()
        except HTTPError:
            reply("API error occurred.")
            raise

        json = response.json()

    if json.get("error"):
        if json["error"]["code"] == 403:
            return "The Books API is off in the Google Developers Console (or check the console)."

        return "Error performing search."

    if json["totalItems"] == 0:
        return "No results found."

    record(db, BOOKS_BUCKET)
    book = json["items"][0]["volumeInfo"]
    title = book["title"]
    try:
        author = book["authors"][0]
    except KeyError:
        try:
            author = book["publisher"]
        except KeyError:
            author = "Unknown Author"

    try:
        description = formatting.truncate_str(book["description"], 130)
    except KeyError:
        description = "No description available."

    try:
        year = book["publishedDate"][:4]
    except KeyError:
        year = "No Year"

    try:
        page_count = book["pageCount"]
        pages = " - " + formatting.pluralize_suffix(page_count, "page")
    except KeyError:
        pages = ""

    link = web.try_shorten(book["infoLink"])

    return "\x02{}\x02 by \x02{}\x02 ({}){} - {} - {}".format(
        title, author, year, pages, description, link
    )
