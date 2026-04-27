"""Custom FunctionTools for the agent that aren't backed by bot commands."""
import asyncio
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pywikibot
import requests
from agents import FunctionTool, RunContextWrapper
from markitdown import MarkItDown
from sqlalchemy import Column, String, Table, Text, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from cloudbot.util import database, web
from cloudbot.util.ai_common import APP_HTML_PROMPT_SUFFIX, upload_html_app
from cloudbot.util.browserless import (
    evaluate_in_page,
    fetch_console_logs,
    is_configured as browserless_configured,
    take_screenshot,
)

_MEMORY_TABLE = Table(
    "agent_memory",
    database.metadata,
    Column("namespace", String(100), primary_key=True),
    Column("key", String(200), primary_key=True),
    Column("value", Text),
    Column("updated_at", String(32)),
    extend_existing=True,
)

_MEMORY_VALUE_MAX = 2000
_MEMORY_SEARCH_LIMIT = 20


def _parse_args(args_json: str) -> dict[str, Any]:
    try:
        return json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return {}


def _parse_namespace(data: dict[str, Any], ctx: RunContextWrapper) -> str:
    return str(data.get("namespace") or ctx.context.chan or "global").strip()[:100]

_MARKDOWN_TEMPLATE_PATH = Path(__file__).parent / "markdown_paste.html"

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
        data = _parse_args(args_json)
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
        data = _parse_args(args_json)
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
        data = _parse_args(args_json)
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
        data = _parse_args(args_json)
        url = str(data.get("url") or "").strip()
        if not url:
            return "(error: url required)"
        if not url.startswith(("http://", "https://")):
            return "(error: url must start with http:// or https://)"

        try:
            loop = asyncio.get_running_loop()
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
        data = _parse_args(args_json)
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
        data = _parse_args(args_json)
        html = str(data.get("html") or "").strip()
        if not html:
            return "(error: html required)"

        try:
            loop = asyncio.get_running_loop()
            url = await loop.run_in_executor(None, upload_html_app, html)
            return url
        except Exception as e:
            return f"(error uploading app: {e})"

    return FunctionTool(
        name="web_app",
        description=(
            "Create and deploy a single-page HTML web app, then return a preview URL. "
            "When called, generate complete self-contained HTML with all CSS and JS inline "
            "(CDN links allowed). "
            "If a previous deploy had bugs (seen via browser_console), fix the ROOT CAUSE in the "
            "HTML you submit — do NOT re-upload the same code expecting different results. "
            "Each call creates a new URL; budget at most 2-3 deploys per user request. "
            + APP_HTML_PROMPT_SUFFIX.strip()
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


def upload_markdown_paste(text: str, title: str = "Response") -> str:
    """Render markdown as a rich HTML page and upload. Returns URL."""
    safe_title = (
        title
        .replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
    template = _MARKDOWN_TEMPLATE_PATH.read_text(encoding="utf-8")
    html = (
        template
        .replace("__TITLE__", safe_title)
        .replace("__TITLE_JSON__", json.dumps(title).replace("</", "<\\/"))
        .replace("__CONTENT_JSON__", json.dumps(text).replace("</", "<\\/"))
    )
    return web.paste(html.encode("utf-8"), ext="html")


def _build_paste_markdown_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        content = str(data.get("content") or "").strip()
        title = str(data.get("title") or "Document").strip()

        if not content:
            return "(error: content required)"

        try:
            loop = asyncio.get_running_loop()
            url = await loop.run_in_executor(
                None, lambda: upload_markdown_paste(content, title)
            )
            return url
        except Exception as e:
            return f"(error uploading: {e})"

    return FunctionTool(
        name="paste_markdown",
        description=(
            "Render markdown as a rich HTML page and return a URL. "
            "Supports: **bold/italic**, headers, tables, code blocks with syntax highlighting, "
            "LaTeX math (inline `$...$` and block `$$...$$`), "
            "Mermaid diagrams (```mermaid fences), "
            "runnable Python blocks (```python — executes in-browser via Pyodide, "
            "numpy/pandas/matplotlib preloaded). "
            "To display a matplotlib plot, save to an in-memory BytesIO and print a data URI line: "
            "`import io, base64; buf = io.BytesIO(); plt.savefig(buf, format='png'); "
            "print('data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode())`. "
            "Any line printed starting with `data:image/` renders as an inline image. "
            "Do NOT use `open('file.png', 'rb')` — there is no filesystem. "
            "and runnable JavaScript blocks (```js — sandboxed iframe, console.log captured). "
            "runnable Lua blocks (```lua — runs via Fengari WASM, print() captured), "
            "runnable SQLite blocks (```sql or ```sqlite — runs in-browser, results shown as table). "
            "Use for long responses, structured reports, math derivations, code tutorials, or anything "
            "that would benefit from rich formatting instead of plain IRC text."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Markdown content to render",
                },
                "title": {
                    "type": "string",
                    "description": "Page title shown in browser tab (optional, default: Document)",
                },
            },
            "required": ["content"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_memory_set_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        key = str(data.get("key") or "").strip()[:200]
        value = str(data.get("value") or "").strip()
        ns = _parse_namespace(data, ctx)

        if not key:
            return "(error: key required)"
        if len(value) > _MEMORY_VALUE_MAX:
            return f"(error: value too long, max {_MEMORY_VALUE_MAX} chars)"

        def _do_upsert() -> None:
            db = database.Session()
            now = datetime.now(timezone.utc).isoformat()
            stmt = (
                sqlite_insert(_MEMORY_TABLE)
                .values(namespace=ns, key=key, value=value, updated_at=now)
                .on_conflict_do_update(
                    index_elements=["namespace", "key"],
                    set_={"value": value, "updated_at": now},
                )
            )
            try:
                db.execute(stmt)
                db.commit()
            except SQLAlchemyError as e:
                db.rollback()
                raise e

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _do_upsert)
        except SQLAlchemyError as e:
            return f"(error storing memory: {e})"
        return f"stored: {ns}/{key}"

    return FunctionTool(
        name="memory_set",
        description=(
            "Store a key-value pair in persistent memory. Use to remember facts, "
            "preferences, or notes across conversations. "
            "namespace defaults to the current channel. "
            f"Value is capped at {_MEMORY_VALUE_MAX} chars."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Memory key (max 200 chars)"},
                "value": {"type": "string", "description": f"Value to store (max {_MEMORY_VALUE_MAX} chars)"},
                "namespace": {"type": "string", "description": "Scope (default: current channel)"},
            },
            "required": ["key", "value"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_memory_get_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        key = str(data.get("key") or "").strip()
        ns = _parse_namespace(data, ctx)

        if not key:
            return "(error: key required)"

        def _do_get() -> Any:
            db = database.Session()
            return db.execute(
                _MEMORY_TABLE.select().where(
                    (_MEMORY_TABLE.c.namespace == ns) &
                    (_MEMORY_TABLE.c.key == key)
                )
            ).first()

        loop = asyncio.get_running_loop()
        try:
            row = await loop.run_in_executor(None, _do_get)
        except SQLAlchemyError as e:
            return f"(error reading memory: {e})"
        if row is None:
            return f"(not found: {ns}/{key})"
        return f"{row['value']} [updated: {row['updated_at'][:16]}]"

    return FunctionTool(
        name="memory_get",
        description=(
            "Retrieve a stored memory by key. "
            "namespace defaults to the current channel. "
            "Returns the value or a not-found message."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Memory key to retrieve"},
                "namespace": {"type": "string", "description": "Scope (default: current channel)"},
            },
            "required": ["key"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_memory_search_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        query = str(data.get("query") or "").strip()
        ns = _parse_namespace(data, ctx)

        if not query:
            return "(error: query required)"

        like = f"%{query}%"

        def _do_search() -> list[Any]:
            db = database.Session()
            return db.execute(
                _MEMORY_TABLE.select().where(
                    (_MEMORY_TABLE.c.namespace == ns) &
                    or_(
                        _MEMORY_TABLE.c.key.ilike(like),
                        _MEMORY_TABLE.c.value.ilike(like),
                    )
                ).order_by(_MEMORY_TABLE.c.updated_at.desc()).limit(_MEMORY_SEARCH_LIMIT)
            ).fetchall()

        loop = asyncio.get_running_loop()
        try:
            matches = await loop.run_in_executor(None, _do_search)
        except SQLAlchemyError as e:
            return f"(error searching memory: {e})"
        if not matches:
            return f"(no memories found for '{query}' in {ns})"
        lines = [f"{r['key']}: {(r['value'] or '')[:200]}" for r in matches]
        return "\n".join(lines)

    return FunctionTool(
        name="memory_search",
        description=(
            "Search stored memories by keyword in key or value. "
            "namespace defaults to the current channel. "
            f"Returns up to {_MEMORY_SEARCH_LIMIT} matching entries."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search for (case-insensitive)"},
                "namespace": {"type": "string", "description": "Scope (default: current channel)"},
            },
            "required": ["query"],
        },
        on_invoke_tool=on_invoke,
    )


def _normalize_url(url: str) -> str:
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _build_browser_screenshot_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        url = _normalize_url(str(data.get("url") or ""))
        if not url:
            return "(error: url required)"
        bot = ctx.context.bot
        if not browserless_configured(bot):
            return "(error: browserless not configured)"

        wait_ms = int(data.get("wait_ms") or 4000)
        try:
            loop = asyncio.get_running_loop()
            png = await loop.run_in_executor(
                None, lambda: take_screenshot(url, bot, extra_wait_ms=wait_ms)
            )
            paste_url = await loop.run_in_executor(
                None, lambda: web.paste(png, ext="png")
            )
            return paste_url
        except requests.HTTPError as e:
            return f"(error: HTTP {e.response.status_code})"
        except Exception as e:
            return f"(error: {e})"

    return FunctionTool(
        name="browser_screenshot",
        description=(
            "Render a URL in a real headless Chrome and upload a PNG screenshot. "
            "Returns the screenshot URL. Use to see what a page actually looks like — "
            "for visual debugging of web apps you build, checking layout, or inspecting "
            "rendered third-party content."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page URL to screenshot"},
                "wait_ms": {
                    "type": "integer",
                    "description": "Extra ms to wait after networkidle2 (default 4000)",
                },
            },
            "required": ["url"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_browser_console_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        url = _normalize_url(str(data.get("url") or ""))
        if not url:
            return "(error: url required)"
        bot = ctx.context.bot
        if not browserless_configured(bot):
            return "(error: browserless not configured)"

        settle_ms = int(data.get("wait_ms") or 2000)
        try:
            loop = asyncio.get_running_loop()
            messages = await loop.run_in_executor(
                None, lambda: fetch_console_logs(url, bot, settle_ms=settle_ms)
            )
        except requests.HTTPError as e:
            return f"(error: HTTP {e.response.status_code})"
        except Exception as e:
            return f"(error: {e})"

        if not messages:
            return "(no console output, no errors)"
        out = json.dumps(messages, ensure_ascii=False, indent=None)
        return out[:4000]

    return FunctionTool(
        name="browser_console",
        description=(
            "Open a URL in a real browser and capture all console.log/warn/error output, "
            "uncaught page exceptions, and failed network requests. "
            "Returns JSON list of {type, text}. "
            "Very useful for debugging web apps you generated — load the paste URL and "
            "see exactly what JS errors fire. Run this AFTER deploying a web_app or "
            "paste_markdown to verify it actually works."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page URL to load"},
                "wait_ms": {
                    "type": "integer",
                    "description": "Ms to wait after load before capturing (default 2000)",
                },
            },
            "required": ["url"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_browser_evaluate_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        url = _normalize_url(str(data.get("url") or ""))
        script = str(data.get("script") or "").strip()
        if not url:
            return "(error: url required)"
        if not script:
            return "(error: script required)"
        bot = ctx.context.bot
        if not browserless_configured(bot):
            return "(error: browserless not configured)"

        settle_ms = int(data.get("wait_ms") or 0)
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: evaluate_in_page(url, script, bot, settle_ms=settle_ms)
            )
        except requests.HTTPError as e:
            return f"(error: HTTP {e.response.status_code})"
        except Exception as e:
            return f"(error: {e})"

        out = json.dumps(result, ensure_ascii=False, default=str)
        return out[:4000]

    return FunctionTool(
        name="browser_evaluate",
        description=(
            "Open a URL in a real browser and run a JS expression/snippet in the page context. "
            "Returns {ok, value} on success or {ok:false, error, stack} on failure. "
            "Use to inspect DOM (e.g. `document.title`, `document.querySelectorAll('.x').length`), "
            "extract data, or test that selectors/state work. "
            "Result must be JSON-serializable; for DOM nodes use `.textContent` or `.outerHTML`."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page URL to load"},
                "script": {
                    "type": "string",
                    "description": "JS expression or block to evaluate in page context",
                },
                "wait_ms": {
                    "type": "integer",
                    "description": "Ms to wait after load before evaluating (default 0)",
                },
            },
            "required": ["url", "script"],
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
    _build_paste_markdown_tool(),
    _build_memory_set_tool(),
    _build_memory_get_tool(),
    _build_memory_search_tool(),
    _build_browser_screenshot_tool(),
    _build_browser_console_tool(),
    _build_browser_evaluate_tool(),
]
