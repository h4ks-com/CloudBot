"""Browserless-backed tools: render screenshots, capture console logs, evaluate JS."""

import json

import requests

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import web
from cloudbot.util.browserless import evaluate_in_page, fetch_console_logs
from cloudbot.util.browserless import is_configured as browserless_configured
from cloudbot.util.browserless import take_screenshot


def _normalize_url(url: str) -> str:
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


@tool(
    name="browser_screenshot",
    description=(
        "Render a URL in a real headless Chrome and upload a PNG screenshot. "
        "Returns the screenshot URL. Use to see what a page actually looks like — "
        "for visual debugging of web apps you build, checking layout, or inspecting "
        "rendered third-party content."
    ),
    schema={
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
)
async def browser_screenshot(ctx, data):
    url = _normalize_url(str(data.get("url") or ""))
    if not url:
        return "(error: url required)"
    bot = ctx.context.bot
    if not browserless_configured(bot):
        return "(error: browserless not configured)"

    wait_ms = int(data.get("wait_ms") or 4000)
    try:
        png = await run_in_executor(
            take_screenshot, url, bot, extra_wait_ms=wait_ms
        )
        paste_url = await run_in_executor(web.paste, png, ext="png")
        return paste_url
    except requests.HTTPError as e:
        return f"(error: HTTP {e.response.status_code})"
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error: {e})"


@tool(
    name="browser_console",
    description=(
        "Open a URL in a real browser and capture all console.log/warn/error output, "
        "uncaught page exceptions, and failed network requests. "
        "Returns JSON list of {type, text}. "
        "Very useful for debugging web apps you generated — load the paste URL and "
        "see exactly what JS errors fire. Run this AFTER deploying a web_app or "
        "paste_markdown to verify it actually works."
    ),
    schema={
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
)
async def browser_console(ctx, data):
    url = _normalize_url(str(data.get("url") or ""))
    if not url:
        return "(error: url required)"
    bot = ctx.context.bot
    if not browserless_configured(bot):
        return "(error: browserless not configured)"

    settle_ms = int(data.get("wait_ms") or 2000)
    try:
        messages = await run_in_executor(
            fetch_console_logs, url, bot, settle_ms=settle_ms
        )
    except requests.HTTPError as e:
        return f"(error: HTTP {e.response.status_code})"
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error: {e})"

    if not messages:
        return "(no console output, no errors)"
    out = json.dumps(messages, ensure_ascii=False, indent=None)
    return out[:4000]


@tool(
    name="browser_evaluate",
    description=(
        "Open a URL in a real browser and run a JS expression/snippet in the page context. "
        "Returns {ok, value} on success or {ok:false, error, stack} on failure. "
        "Use to inspect DOM (e.g. `document.title`, `document.querySelectorAll('.x').length`), "
        "extract data, or test that selectors/state work. "
        "Result must be JSON-serializable; for DOM nodes use `.textContent` or `.outerHTML`."
    ),
    schema={
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
)
async def browser_evaluate(ctx, data):
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
        result = await run_in_executor(
            evaluate_in_page, url, script, bot, settle_ms=settle_ms
        )
    except requests.HTTPError as e:
        return f"(error: HTTP {e.response.status_code})"
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error: {e})"

    out = json.dumps(result, ensure_ascii=False, default=str)
    return out[:4000]
