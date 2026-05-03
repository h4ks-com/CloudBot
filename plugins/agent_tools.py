"""Custom FunctionTools for the agent that aren't backed by bot commands."""
import asyncio
import base64
import importlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pywikibot
import requests
from agents import FunctionTool, RunContextWrapper
from markitdown import MarkItDown
from openai import AsyncOpenAI
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

logger = logging.getLogger("cloudbot")

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


_VISION_IMAGE_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB
_VISION_MAX_TOKENS = 512


def _build_describe_image_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        url = str(data.get("url") or "").strip()
        question = str(data.get("question") or "Describe this image in detail.").strip()

        if not url:
            return "(error: url required)"
        if not url.startswith(("http://", "https://")):
            return "(error: url must start with http:// or https://)"

        bot = ctx.context.bot
        vision_cfg = (bot.config.get("plugins") or {}).get("agent", {}).get("vision") or {}
        base_url = vision_cfg.get("base_url") or "https://api.z.ai/api/paas/v4"
        model = vision_cfg.get("model") or "glm-4.6v-flash"
        api_key_path = vision_cfg.get("api_key_config_path") or "z_ai"
        api_key = bot.config.get_api_key(api_key_path)
        if not api_key:
            return f"(error: api key '{api_key_path}' not configured)"

        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(url, timeout=15, headers={"User-Agent": "CloudBot/1.0"}),
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            return f"(error downloading image: {e})"

        if len(resp.content) > _VISION_IMAGE_SIZE_LIMIT:
            return f"(error: image too large, max {_VISION_IMAGE_SIZE_LIMIT // 1024 // 1024} MB)"

        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not content_type.startswith("image/"):
            content_type = "image/jpeg"

        data_uri = f"data:{content_type};base64,{base64.b64encode(resp.content).decode()}"

        try:
            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            completion = await client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": question},
                    ],
                }],
                max_tokens=_VISION_MAX_TOKENS,
            )
            return completion.choices[0].message.content or "(no description)"
        except Exception as e:
            return f"(error calling vision model: {e})"

    return FunctionTool(
        name="describe_image",
        description=(
            "Download an image URL and describe its contents using a vision model. "
            "Use when a user shares an image link and asks what's in it, or when visual "
            "context would help answer a question. Accepts jpg/png/gif/webp URLs. "
            "Optionally accepts a specific question to answer about the image."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Direct URL to the image"},
                "question": {
                    "type": "string",
                    "description": "Specific question to answer about the image (default: describe it)",
                },
            },
            "required": ["url"],
        },
        on_invoke_tool=on_invoke,
    )


def _extract_mcp_content(result: dict) -> str:
    """Extract meaningful text from a GitHub MCP tool result dict.

    GitHub MCP returns two content items per file call:
      - type='text':     a metadata summary ("successfully downloaded…")
      - type='resource': the actual file text in resource.text

    We prefer resource.text when present; fall back to plain text items.
    """
    content = result.get("content", [])
    if not isinstance(content, list):
        return json.dumps(result)[:8000]
    resource_parts = [
        c["resource"]["text"]
        for c in content
        if c.get("type") == "resource" and isinstance(c.get("resource"), dict) and "text" in c["resource"]
    ]
    if resource_parts:
        return "\n".join(resource_parts)
    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(text_parts) if text_parts else json.dumps(result)[:8000]


_SSE_FIELDS = ("data:", "event:", "id:", "retry:")
_SHA_PATTERN = re.compile(r"\(SHA:\s*([0-9a-f]{6,64})\)")


def _extract_file_sha(result: dict) -> str | None:
    """Pull blob SHA out of GitHub MCP get_file_contents text content.

    The MCP server formats the success note as: "successfully downloaded
    text file (SHA: <sha>)..." — we scan all text items for that pattern.
    """
    content = result.get("content", [])
    if not isinstance(content, list):
        return None
    for c in content:
        if not isinstance(c, dict) or c.get("type") != "text":
            continue
        match = _SHA_PATTERN.search(c.get("text") or "")
        if match:
            return match.group(1)
    return None


def _parse_sse(text: str) -> dict | None:
    """Parse an SSE response body and return the first JSON-RPC result dict.

    GitHub MCP embeds literal (unescaped) newlines inside JSON string values,
    so one SSE data: event spans many physical lines. We collect continuation
    lines (non-SSE-field, non-blank) and join with the JSON newline escape
    sequence so json.loads can parse the reassembled chunk.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("data:"):
            i += 1
            continue
        chunk_parts = [lines[i][5:]]
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if not ln or any(ln.startswith(f) for f in _SSE_FIELDS):
                break
            chunk_parts.append(ln)
            j += 1
        chunk = "\\n".join(chunk_parts).strip()
        i = j
        if not chunk or chunk == "[DONE]":
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "result" in parsed:
            result = parsed["result"]
            return result if isinstance(result, dict) else {}
    return None


async def _github_mcp_call_raw(event, tool_name: str, args: dict) -> dict | str:
    """JSON-RPC 2.0 call returning the raw MCP result dict, or an error string.

    Returns dict on success, or a string starting with "(error" / "(mcp error"
    on failure. Callers that need the extracted text use _github_mcp_call.
    """
    bot = event.bot
    if not event.conn.permissions.has_perm_mask(event.mask, "botcontrol"):
        return f"(error: GitHub MCP tools require botcontrol permission — {event.nick} is not authorised)"
    cfg = ((bot.config.get("plugins") or {}).get("agent") or {}).get("github_mcp") or {}
    if not cfg.get("enabled", True):
        return "(error: github_mcp disabled in config)"
    url = cfg.get("url") or ""
    api_key = bot.config.get_api_key(cfg.get("api_key_config_path") or "github_mcp_bot")
    github_token = bot.config.get_api_key(cfg.get("github_token_config_path") or "github")
    if not url or not api_key or not github_token:
        return "(error: github_mcp url, api key, or github token not configured)"

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {github_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cloudbot-agent", "version": "1.0"},
        },
    }
    loop = asyncio.get_running_loop()
    try:
        resp = await loop.run_in_executor(
            None, lambda: requests.post(url, json=init_payload, headers=headers, timeout=10)
        )
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id", "")

        call_headers = {**headers}
        if session_id:
            call_headers["mcp-session-id"] = session_id
        call_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        }
        resp2 = await loop.run_in_executor(
            None, lambda: requests.post(url, json=call_payload, headers=call_headers, timeout=30)
        )
        resp2.raise_for_status()
        text = resp2.text

        if "data:" in text:
            result = _parse_sse(text)
            return result if result is not None else "(no result in SSE stream)"

        data = resp2.json()
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return f"(mcp error: {msg})"
        result = data.get("result", {}) if isinstance(data, dict) else {}
        return result if isinstance(result, dict) else {}
    except requests.RequestException as e:
        logger.warning("github_mcp %s request failed: %s", tool_name, e)
        return f"(error calling github mcp: {e})"
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        logger.exception("github_mcp %s parsing failed", tool_name)
        return f"(error parsing github mcp response: {type(e).__name__}: {str(e)[:200]})"


async def _github_mcp_call(event, tool_name: str, args: dict) -> str:
    """JSON-RPC 2.0 call returning extracted text content (or error string)."""
    raw = await _github_mcp_call_raw(event, tool_name, args)
    if isinstance(raw, str):
        return raw
    return _extract_mcp_content(raw)


def _build_list_repo_files_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        repo = str(data.get("repo") or "").strip()
        path = str(data.get("path") or "").strip()
        branch = str(data.get("branch") or "main").strip()
        if not repo:
            return "(error: repo required, e.g. 'owner/repo')"
        return await _github_mcp_call(
            ctx.context, "get_file_contents",
            {"owner": repo.split("/")[0], "repo": repo.split("/")[-1],
             "path": path, "ref": branch},
        )

    return FunctionTool(
        name="list_repo_files",
        description=(
            "List files and directories in a GitHub repo at a given path. "
            "repo format: 'owner/repo'. path defaults to repo root. "
            "Use to explore the codebase structure before reading or editing files."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "GitHub repo in 'owner/repo' format"},
                "path": {"type": "string", "description": "Directory path (default: root)"},
                "branch": {"type": "string", "description": "Branch name (default: main)"},
            },
            "required": ["repo"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_read_github_file_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        repo = str(data.get("repo") or "").strip()
        path = str(data.get("path") or "").strip()
        branch = str(data.get("branch") or "main").strip()
        start_line = data.get("start_line")
        if not repo or not path:
            return "(error: repo and path required)"
        raw_result = await _github_mcp_call_raw(
            ctx.context, "get_file_contents",
            {"owner": repo.split("/")[0], "repo": repo.split("/")[-1],
             "path": path, "ref": branch},
        )
        if isinstance(raw_result, str):
            return raw_result
        body = _extract_mcp_content(raw_result)
        sha = _extract_file_sha(raw_result)
        sha_line = f"SHA: {sha}\n" if sha else ""
        if start_line is not None:
            try:
                center = int(start_line)
                lines = body.splitlines()
                lo = max(0, center - 30)
                hi = min(len(lines), center + 120)
                excerpt = "\n".join(f"{lo+i+1}: {l}" for i, l in enumerate(lines[lo:hi]))
                return f"{sha_line}(lines {lo+1}-{hi} of {len(lines)})\n{excerpt}"
            except (ValueError, AttributeError):
                pass
        return f"{sha_line}{body[:8000]}"

    return FunctionTool(
        name="read_github_file",
        description=(
            "Read the contents of a file from any GitHub repo. "
            "repo format: 'owner/repo'. Returns raw file text (up to 8000 chars from top), "
            "with a 'SHA: <hash>' header line — the SHA is the file's blob SHA on that branch. "
            "If you know the line number of interest (e.g. from ghsource), pass start_line "
            "to get ±100 lines around that line instead of reading from the top. "
            "Always read a file before editing it."
        ),
        params_json_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "repo": {"type": "string", "description": "owner/repo, e.g. 'h4ks-com/CloudBot'"},
                "path": {"type": "string", "description": "File path, e.g. 'plugins/weather.py'"},
                "branch": {"type": "string", "description": "Branch (default: main)"},
                "start_line": {"type": "integer", "description": "Line number from ghsource #L (returns ±100 lines)"},
            },
            "required": ["repo", "path"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_search_github_code_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        query = str(data.get("query") or "").strip()
        repo = str(data.get("repo") or "").strip()
        if not query:
            return "(error: query required)"
        args: dict[str, Any] = {"query": query}
        if repo:
            args["query"] = f"{query} repo:{repo}"
        return await _github_mcp_call(ctx.context, "search_code", args)

    return FunctionTool(
        name="search_github_code",
        description=(
            "Search code across GitHub using GitHub code search. "
            "Optionally scope to a specific repo ('owner/repo'). "
            "Use to find where a function or pattern is defined before editing."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (GitHub code search syntax)"},
                "repo": {"type": "string", "description": "Limit to repo 'owner/repo' (optional)"},
            },
            "required": ["query"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_fork_github_repo_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        repo = str(data.get("repo") or "").strip()
        if not repo:
            return "(error: repo required)"
        result = await _github_mcp_call(
            ctx.context, "fork_repository",
            {"owner": repo.split("/")[0], "repo": repo.split("/")[-1]},
        )
        return result

    return FunctionTool(
        name="fork_github_repo",
        description=(
            "Fork a GitHub repo to the authenticated account. "
            "IMPORTANT: fork creation is async — wait ~10 seconds before using the fork. "
            "Use this before editing if you don't have direct write access to the repo."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo to fork in 'owner/repo' format"},
            },
            "required": ["repo"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_create_github_branch_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        repo = str(data.get("repo") or "").strip()
        branch = str(data.get("branch") or "").strip()
        base = str(data.get("base") or "main").strip()
        if not repo or not branch:
            return "(error: repo and branch required)"
        return await _github_mcp_call(
            ctx.context, "create_branch",
            {"owner": repo.split("/")[0], "repo": repo.split("/")[-1],
             "branch": branch, "from_branch": base},
        )

    return FunctionTool(
        name="create_github_branch",
        description=(
            "Create a new git branch in a GitHub repo. "
            "Use a descriptive name like 'fix/command-name' or 'add/feature-name'. "
            "Always create a branch before editing files."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/repo' format"},
                "branch": {"type": "string", "description": "New branch name"},
                "base": {"type": "string", "description": "Base branch to create from (default: main)"},
            },
            "required": ["repo", "branch"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_edit_github_file_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        repo = str(data.get("repo") or "").strip()
        path = str(data.get("path") or "").strip()
        content = str(data.get("content") or "")
        message = str(data.get("message") or "Update file via CloudBot AGI").strip()
        branch = str(data.get("branch") or "main").strip()
        sha = str(data.get("sha") or "").strip()
        if not repo or not path or not content:
            return "(error: repo, path, and content required)"
        owner, repo_name = repo.split("/")[0], repo.split("/")[-1]
        if not sha:
            probe = await _github_mcp_call_raw(
                ctx.context, "get_file_contents",
                {"owner": owner, "repo": repo_name, "path": path, "ref": branch},
            )
            if isinstance(probe, dict):
                sha = _extract_file_sha(probe) or ""
        args = {"owner": owner, "repo": repo_name, "path": path,
                "content": content, "message": message, "branch": branch}
        if sha:
            args["sha"] = sha
        return await _github_mcp_call(ctx.context, "create_or_update_file", args)

    return FunctionTool(
        name="edit_github_file",
        description=(
            "Create or overwrite a file in a GitHub repo on the given branch. "
            "Provide the COMPLETE new file content — this is a full replacement, not a patch. "
            "If the file already exists on the branch its blob SHA is required by GitHub; "
            "this tool auto-fetches the current SHA, so you do NOT need to supply it. "
            "Optionally pass sha if you already have it from read_github_file. "
            "Always read_github_file first to get the current content before editing. "
            "Always create_github_branch before editing."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/repo' format"},
                "path": {"type": "string", "description": "File path, e.g. 'plugins/foo.py'"},
                "content": {"type": "string", "description": "Complete new file content"},
                "message": {"type": "string", "description": "Commit message"},
                "branch": {"type": "string", "description": "Branch to commit to"},
                "sha": {"type": "string", "description": "Optional blob SHA (auto-fetched if omitted)"},
            },
            "required": ["repo", "path", "content", "branch"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_open_github_pr_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        data = _parse_args(args_json)
        repo = str(data.get("repo") or "").strip()
        title = str(data.get("title") or "").strip()
        body = str(data.get("body") or "Automated PR by CloudBot AGI").strip()
        head = str(data.get("head") or "").strip()
        base = str(data.get("base") or "main").strip()
        draft = bool(data.get("draft", False))
        if not repo or not title or not head:
            return "(error: repo, title, and head required)"
        return await _github_mcp_call(
            ctx.context, "create_pull_request",
            {"owner": repo.split("/")[0], "repo": repo.split("/")[-1],
             "title": title, "body": body, "head": head, "base": base, "draft": draft},
        )

    return FunctionTool(
        name="open_github_pr",
        description=(
            "Open a GitHub pull request from head branch into base branch. "
            "For cross-fork PRs use 'forkowner:branchname' as head. "
            "Set draft=true for work-in-progress PRs. "
            "Returns the PR URL — report it to the user."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Target repo in 'owner/repo' format"},
                "title": {"type": "string", "description": "PR title"},
                "body": {"type": "string", "description": "PR description"},
                "head": {"type": "string", "description": "Source branch (or 'forkowner:branch')"},
                "base": {"type": "string", "description": "Target branch (default: main)"},
                "draft": {"type": "boolean", "description": "Open as draft PR (default: false)"},
            },
            "required": ["repo", "title", "head"],
        },
        on_invoke_tool=on_invoke,
    )


def _build_list_bot_commands_tool() -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        event = ctx.context
        try:
            cmds = sorted(event.bot.plugin_manager.commands.keys())
        except AttributeError:
            return "(error: command list unavailable)"
        return ", ".join(cmds)

    return FunctionTool(
        name="list_bot_commands",
        description=(
            "Return a sorted list of all bot command names (without dot prefix). "
            "Use when ghsource says a command is not found and you need to find the correct name — "
            "call this, scan the list for a close match or spelling variant, then call ghsource again."
        ),
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=on_invoke,
    )


CUSTOM_TOOLS: list[FunctionTool] = [
    _build_chat_history_tool(),
    _build_search_history_tool(),
    _build_list_bot_commands_tool(),
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
    _build_describe_image_tool(),
    _build_list_repo_files_tool(),
    _build_read_github_file_tool(),
    _build_search_github_code_tool(),
    _build_fork_github_repo_tool(),
    _build_create_github_branch_tool(),
    _build_edit_github_file_tool(),
    _build_open_github_pr_tool(),
]
