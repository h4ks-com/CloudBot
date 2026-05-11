"""Agent tools for web-related operations."""

import requests

from cloudbot.agent.registry import tool
from cloudbot.util import run_in_executor

APP_HTML_PROMPT_SUFFIX = """
Make sure to put everything in a single html file so it can be a single code block meant to be directly used in a browser as it is. Do not explain, just show the code.
"""

_MARKDOWN_TEMPLATE_PATH = "cloudbot/agent/tools/_markdown_template.html"


async def upload_markdown_paste(content, title):
    """Upload rendered markdown to the paste service."""
    import os
    template_path = os.path.join(
        os.path.dirname(__file__), "_markdown_template.html"
    )
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
    html = template.replace("{{CONTENT}}", content).replace(
        "{{TITLE}}", title
    )
    resp = requests.post(
        "https://s.h4ks.com",
        files={"file": ("index.html", html, "text/html")},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text.strip()


async def upload_html_app(html):
    """Upload an HTML app to the paste service."""
    resp = requests.post(
        "https://s.h4ks.com",
        files={"file": ("index.html", html, "text/html")},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text.strip()


@tool(
    name="web_fetch",
    description=(
        "Fetch a URL and return its content as plain text/markdown. "
        "Use for reading articles, docs, GitHub pages, or any link shared in chat."
    ),
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
    from cloudbot.util.web import user_agent

    url = str(data.get("url") or "").strip()
    if not url:
        return "(error: url required)"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        import html
        import re

        resp = requests.get(
            url,
            headers={"User-Agent": user_agent()},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
        text = resp.text

        # Strip HTML tags for a rough text extraction
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()

        return text[:4000]
    except requests.RequestException as e:
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
        "The HTML is automatically saved to memory so it can be retrieved later — "
        "use the webapp_get_html tool to get the last deployed HTML source."
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
        # Save the HTML to memory so it can be retrieved for persisting
        await ctx.memory.set("last_webapp_html", html)
        await ctx.memory.set("last_webapp_url", url)
        return url
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error uploading app: {e})"


@tool(
    name="webapp_get_html",
    description=(
        "Retrieve the HTML source of the last web_app deployment. "
        "Use this when the user asks to persist/make permanent a previously deployed web app. "
        "Returns the full HTML that was last submitted to web_app, along with the URL it was "
        "deployed to. This avoids having to regenerate the app from scratch."
    ),
    schema={
        "type": "object",
        "properties": {},
    },
)
async def webapp_get_html(ctx, data):
    html = await ctx.memory.get("last_webapp_html")
    url = await ctx.memory.get("last_webapp_url")
    if not html:
        return "(no web_app deployment found in memory — the app may have been deployed in a previous session)"
    return f"URL: {url}\n\nHTML:\n{html}"


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
