"""Web fetching, HTML app deployment, and markdown paste rendering tools."""

import json
from pathlib import Path

import requests
from markitdown import MarkItDown

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import web
from cloudbot.util.ai_common import APP_HTML_PROMPT_SUFFIX, upload_html_app

# Template lives in plugins/ alongside the agent plugin; resolve from project
# root so this module can move without breaking the lookup.
_MARKDOWN_TEMPLATE_PATH = (
    Path(__file__).parents[3] / "plugins" / "markdown_paste.html"
)

_md = MarkItDown()


def upload_markdown_paste(text: str, title: str = "Response") -> str:
    """Render markdown as a rich HTML page and upload. Returns URL."""
    safe_title = (
        title.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    template = _MARKDOWN_TEMPLATE_PATH.read_text(encoding="utf-8")
    html = (
        template.replace("__TITLE__", safe_title)
        .replace("__TITLE_JSON__", json.dumps(title).replace("</", "<\\/"))
        .replace("__CONTENT_JSON__", json.dumps(text).replace("</", "<\\/"))
    )
    return web.paste(html.encode("utf-8"), ext="html")


@tool(
    name="web_fetch",
    description="Fetch a URL and return its content as plain text/markdown. Use for reading articles, docs, GitHub pages, or any link shared in chat.",
    schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch (must start with http:// or https://)",
            }
        },
        "required": ["url"],
    },
)
async def web_fetch(ctx, data):
    url = str(data.get("url") or "").strip()
    if not url:
        return "(error: url required)"
    if not url.startswith(("http://", "https://")):
        return "(error: url must start with http:// or https://)"

    try:
        result = await run_in_executor(_md.convert, url)
        text = (result.text_content or "").strip()
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error fetching {url}: {e})"

    if not text:
        return "(no text content extracted)"
    return text[:4000]


@tool(
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
    schema={
        "type": "object",
        "properties": {
            "html": {
                "type": "string",
                "description": "Complete single-file HTML source for the app",
            }
        },
        "required": ["html"],
    },
)
async def web_app(ctx, data):
    html = str(data.get("html") or "").strip()
    if not html:
        return "(error: html required)"

    try:
        url = await run_in_executor(upload_html_app, html)
        return url
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error uploading app: {e})"


@tool(
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
    schema={
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
)
async def paste_markdown(ctx, data):
    content = str(data.get("content") or "").strip()
    title = str(data.get("title") or "Document").strip()

    if not content:
        return "(error: content required)"

    try:
        url = await run_in_executor(upload_markdown_paste, content, title)
        return url
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error uploading: {e})"
