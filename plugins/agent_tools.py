"""Custom FunctionTools for the agent that aren't backed by bot commands."""
import asyncio
import importlib
import json
from datetime import datetime

import pywikibot
import requests
from agents import FunctionTool, RunContextWrapper
from markitdown import MarkItDown

from cloudbot.util.ai_common import APP_HTML_PROMPT_SUFFIX, upload_html_app

WIKI_API = "https://wiki.h4ks.com/api.php"
WIKI_URL = "https://wiki.h4ks.com"


def _patch_wiki_input(wiki_password: str) -> None:
    _orig = pywikibot.input
    def mock_input(question, password=False, default="", force=False):
        if password:
            return wiki_password
        return _orig(question, password=password, default=default, force=force)
    pywikibot.input = mock_input


def _build_chat_history_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            data = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            data = {}

        n = min(int(data.get("n") or 20), 100)
        event = ctx.context

        try:
            history = list(event.conn.history[event.chan])
        except (KeyError, AttributeError):
            return "(no history available)"

        recent = history[-n:]
        lines = []
        for nick, timestamp, msg in recent:
            msg = msg.replace("\x01ACTION ", "* ").replace("\x01", "")
            ts = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
            lines.append(f"[{ts}] <{nick}> {msg}")

        return "\n".join(lines) if lines else "(no messages in history)"

    return FunctionTool(
        name="chat_history",
        description="Get recent chat messages from the current IRC channel. Use to understand conversation context before answering.",
        params_json_schema={
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of recent messages to fetch (default 20, max 100)",
                }
            },
        },
        on_invoke_tool=on_invoke,
    )


def _build_wiki_read_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            data = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            data = {}

        title = str(data.get("title") or "").strip()
        if not title:
            return "(error: title required)"

        try:
            resp = requests.get(WIKI_API, params={
                "action": "query",
                "titles": title,
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "format": "json",
            }, timeout=10)
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

    return FunctionTool(
        name="wiki_read",
        description=f"Read a page from the h4ks wiki ({WIKI_URL}). Returns wikitext content and URL.",
        params_json_schema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Page title to read",
                }
            },
            "required": ["title"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_wiki_write_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            data = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            data = {}

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

        _patch_wiki_input(password)

        for attempt in range(3):
            try:
                site = pywikibot.Site(url=WIKI_API, user=username)
                page = pywikibot.Page(site, title)
                action = "Editing" if page.exists() else "Creating"
                page.text = content
                page.save(summary)
                url = f"{WIKI_URL}/wiki/{title.replace(' ', '_')}"
                return f"{action} done: {url}"
            except Exception as e:
                if attempt == 2:
                    return f"(error saving wiki page: {e})"
                importlib.reload(pywikibot)
                _patch_wiki_input(password)

        return "(error: unknown failure)"

    return FunctionTool(
        name="wiki_write",
        description=f"Create or edit a page on the h4ks wiki ({WIKI_URL}). Provide mediawiki markup as content. Use wiki_read first if page may already exist.",
        params_json_schema={
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
        on_invoke_tool=on_invoke,
    )


def _build_web_fetch_tool() -> FunctionTool:
    _md = MarkItDown()

    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            data = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            data = {}

        url = str(data.get("url") or "").strip()
        if not url:
            return "(error: url required)"
        if not url.startswith(("http://", "https://")):
            return "(error: url must start with http:// or https://)"

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _md.convert, url)
            text = (result.text_content or "").strip()
        except Exception as e:
            return f"(error fetching {url}: {e})"

        if not text:
            return "(no text content extracted)"
        return text[:4000]

    return FunctionTool(
        name="web_fetch",
        description="Fetch a URL and return its content as plain text/markdown. Use for reading articles, docs, GitHub pages, or any link shared in chat.",
        params_json_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch (must start with http:// or https://)",
                }
            },
            "required": ["url"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_search_history_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            data = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            data = {}

        query = str(data.get("query") or "").strip().lower()
        if not query:
            return "(error: query required)"

        event = ctx.context
        try:
            history = list(event.conn.history[event.chan])
        except (KeyError, AttributeError):
            return "(no history available)"

        matches = []
        for nick, timestamp, msg in history:
            if query in msg.lower():
                msg = msg.replace("\x01ACTION ", "* ").replace("\x01", "")
                ts = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
                matches.append(f"[{ts}] <{nick}> {msg}")

        if not matches:
            return f"(no messages found containing '{query}')"
        return "\n".join(matches[-30:])

    return FunctionTool(
        name="search_history",
        description="Search recent channel messages for a keyword or phrase. Returns matching lines with timestamps.",
        params_json_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search for (case-insensitive)",
                }
            },
            "required": ["query"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_web_app_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            data = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            data = {}

        html = str(data.get("html") or "").strip()
        if not html:
            return "(error: html required)"

        try:
            loop = asyncio.get_event_loop()
            url = await loop.run_in_executor(None, upload_html_app, html)
            return url
        except Exception as e:
            return f"(error uploading app: {e})"

    return FunctionTool(
        name="web_app",
        description=(
            "Create and deploy a single-page HTML web app, then return a preview URL. "
            "When called, generate complete self-contained HTML with all CSS and JS inline "
            "(CDN links allowed). " + APP_HTML_PROMPT_SUFFIX.strip()
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "html": {
                    "type": "string",
                    "description": "Complete single-file HTML source for the app",
                }
            },
            "required": ["html"],
        },
        on_invoke_tool=on_invoke,
    )


CUSTOM_TOOLS: list[FunctionTool] = [
    _build_chat_history_tool(),
    _build_search_history_tool(),
    _build_wiki_read_tool(),
    _build_wiki_write_tool(),
    _build_web_fetch_tool(),
    _build_web_app_tool(),
]
