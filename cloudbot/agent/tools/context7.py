"""Context7 MCP integration — fetch library documentation from context7.com.

Provides a `context7` tool that resolves library docs via the Context7 API
(https://context7.com). This gives the agent real-time access to up-to-date
library/framework documentation without needing to fetch individual URLs.

Context7 API endpoint: https://api.context7.com/docs
Returns structured markdown documentation for any supported library.
"""

import logging

import requests

from cloudbot.agent.common import run_in_executor, safe_tool
from cloudbot.agent.registry import tool

logger = logging.getLogger("cloudbot")

_CONTEXT7_BASE = "https://api.context7.com"


def _fetch_context7_docs(query: str) -> str:
    """Call Context7 API and return docs as markdown text."""
    url = f"{_CONTEXT7_BASE}/docs"
    try:
        r = requests.get(
            url,
            params={"query": query},
            headers={
                "Accept": "application/json",
                "User-Agent": "CloudBot/1.0",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        return f"(error fetching context7 docs for '{query}': {e})"
    except (KeyError, ValueError) as e:
        return f"(error parsing context7 response: {e})"

    # Context7 returns a list of doc entries; format them
    if isinstance(data, list):
        if not data:
            return f"(no context7 results found for '{query}')"
        parts = []
        for entry in data[:5]:  # cap at 5 entries to stay within limits
            title = entry.get("title", "Untitled")
            content = entry.get("content", "") or entry.get("text", "") or ""
            source = entry.get("source", "") or entry.get("url", "")
            block = f"## {title}\n"
            if source:
                block += f"*Source: {source}*\n\n"
            # Truncate long entries
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            block += content
            parts.append(block)
        return "\n\n---\n\n".join(parts)
    elif isinstance(data, dict):
        # Single result or error wrapper
        content = data.get("content", "") or data.get("text", "") or str(data)
        if len(content) > 4000:
            content = content[:4000] + "\n... (truncated)"
        return content
    else:
        return str(data)[:4000]


@tool(
    name="context7",
    description=(
        "Fetch up-to-date library/framework documentation from Context7. "
        "Pass a library name, framework name, or specific topic (e.g. 'react hooks', "
        "'fastapi dependency injection', 'next.js app router', 'tailwind css utilities'). "
        "Returns structured markdown docs with code examples. "
        "Use when you need accurate, current API documentation for any programming "
        "library or framework."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Library name, framework, or topic to look up docs for "
                    "(e.g. 'react', 'fastapi', 'vue composition api')"
                ),
            }
        },
        "required": ["query"],
    },
    wrap_errors=True,
)
async def context7(ctx, data):
    query = str(data.get("query") or "").strip()
    if not query:
        return "(error: query required)"

    result = await run_in_executor(_fetch_context7_docs, query)
    return result
