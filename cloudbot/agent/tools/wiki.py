"""Wiki read/write tools targeting the h4ks MediaWiki instance."""

import importlib

import pywikibot
import pywikibot.exceptions
import requests

from cloudbot.agent.registry import tool

WIKI_API = "https://wiki.h4ks.com/api.php"
WIKI_URL = "https://wiki.h4ks.com"


def patch_wiki_input(wiki_password: str) -> None:
    """Replace pywikibot.input so non-interactive password prompts return the configured password.

    Public so other plugins (e.g. plugins/gpt.py) can reuse the same patch.
    """
    _orig = pywikibot.input

    def mock_input(question, password=False, default="", force=False):
        if password:
            return wiki_password
        return _orig(question, password=password, default=default, force=force)

    pywikibot.input = mock_input


@tool(
    name="wiki_read",
    description=f"Read a page from the h4ks wiki ({WIKI_URL}). Returns wikitext content and URL.",
    schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Page title to read",
            }
        },
        "required": ["title"],
    },
)
async def wiki_read(ctx, data):
    title = str(data.get("title") or "").strip()
    if not title:
        return "(error: title required)"

    try:
        resp = requests.get(
            WIKI_API,
            params={
                "action": "query",
                "titles": title,
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "format": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        pages = resp.json()["query"]["pages"]
        page = next(iter(pages.values()))
        if "missing" in page:
            return f"(page '{title}' does not exist)"
        content = page["revisions"][0]["slots"]["main"]["*"]
        url = f"{WIKI_URL}/wiki/{title.replace(' ', '_')}"
        return f"URL: {url}\n\n{content[:3000]}"
    except requests.RequestException as e:
        return f"(error fetching wiki page: {e})"


@tool(
    name="wiki_write",
    description=f"Create or edit a page on the h4ks wiki ({WIKI_URL}). Provide mediawiki markup as content. Use wiki_read first if page may already exist.",
    schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Page title to create or edit",
            },
            "content": {
                "type": "string",
                "description": "Full page content in mediawiki markup",
            },
            "summary": {
                "type": "string",
                "description": "Edit summary (optional)",
            },
        },
        "required": ["title", "content"],
    },
)
async def wiki_write(ctx, data):
    title = str(data.get("title") or "").strip()
    content = str(data.get("content") or "").strip()
    summary = str(data.get("summary") or "Edited by CloudBot agent").strip()

    if not title:
        return "(error: title required)"
    if not content:
        return "(error: content required)"

    event = ctx.context
    bot = event.bot
    username = bot.config.get_api_key("wiki_username")
    password = bot.config.get_api_key("wiki_password")

    if not username or not password:
        return "(error: wiki_username or wiki_password not configured)"

    patch_wiki_input(password)

    for attempt in range(3):
        try:
            site = pywikibot.Site(url=WIKI_API, user=username)
            page = pywikibot.Page(site, title)
            action = "Editing" if page.exists() else "Creating"
            page.text = content
            page.save(summary)
            url = f"{WIKI_URL}/wiki/{title.replace(' ', '_')}"
            return f"{action} done: {url}"
        except (
            pywikibot.exceptions.Error,
            requests.RequestException,
            OSError,
            ValueError,
        ) as e:
            if attempt == 2:
                return f"(error saving wiki page: {e})"
            importlib.reload(pywikibot)
            patch_wiki_input(password)

    return "(error: unknown failure)"
