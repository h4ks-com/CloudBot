"""Context7 documentation lookup tools.

Free tier works without an API key (1k calls/month); a configured key
in `plugins.agent.context7.api_key_config_path` lifts the limit.
"""

import logging
from typing import TypedDict, cast

import httpx
from typing_extensions import NotRequired

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool

logger = logging.getLogger("cloudbot")

_BASE_URL = "https://context7.com"
_SEARCH_URL = f"{_BASE_URL}/api/v2/libs/search"
_CONTEXT_URL = f"{_BASE_URL}/api/v2/context"
_TIMEOUT = 15.0
_MAX_RESULTS = 3
_MAX_DOC_CHARS = 6000


class LibraryResult(TypedDict):
    id: str
    title: NotRequired[str]
    description: NotRequired[str]
    versions: NotRequired[list[str]]


class CodeBlock(TypedDict):
    language: NotRequired[str]
    code: NotRequired[str]


class CodeSnippet(TypedDict):
    codeTitle: NotRequired[str]
    codeLanguage: NotRequired[str]
    codeDescription: NotRequired[str]
    codeList: NotRequired[list[CodeBlock]]


class InfoSnippet(TypedDict):
    breadcrumb: NotRequired[str]
    content: NotRequired[str]


class DocsResponse(TypedDict):
    codeSnippets: NotRequired[list[CodeSnippet]]
    infoSnippets: NotRequired[list[InfoSnippet]]
    rules: NotRequired[dict[str, str] | str]


def _get_api_key(bot) -> str | None:
    cfg = (bot.config.get("plugins") or {}).get("agent", {}).get(
        "context7"
    ) or {}
    key_path = cfg.get("api_key_config_path") or ""
    if not key_path:
        return None
    key = bot.config.get_api_key(key_path)
    return key if isinstance(key, str) else None


def _headers(api_key: str | None) -> dict[str, str]:
    h = {"Accept": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _search_sync(
    library_name: str, query: str, api_key: str | None
) -> list[LibraryResult]:
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        r = client.get(
            _SEARCH_URL,
            params={"libraryName": library_name, "query": query},
            headers=_headers(api_key),
        )
        r.raise_for_status()
        data = r.json()
    results = data.get("results") or []
    return results[:_MAX_RESULTS]


def _format_search(results: list[LibraryResult]) -> str:
    if not results:
        return "No libraries found."
    parts = []
    for r in results:
        versions = ", ".join(r.get("versions") or [])
        line = (
            f"- {r['id']}  ({r.get('title', '')})  {r.get('description', '')}"
        )
        if versions:
            line += f"  [versions: {versions}]"
        parts.append(line)
    return "\n".join(parts)


@tool(
    name="context7_search",
    description=(
        "Search for a library on Context7 by name. Returns the library ID, "
        "description, and available versions. Use this FIRST to resolve a "
        "library name to a Context7 ID, then call context7_docs with that ID."
    ),
    schema={
        "type": "object",
        "properties": {
            "library_name": {
                "type": "string",
                "description": "Library or package name (e.g. 'react', 'next.js', 'express')",
            },
            "query": {
                "type": "string",
                "description": "What you need from this library — used for relevance ranking",
            },
        },
        "required": ["library_name", "query"],
    },
)
async def context7_search(ctx, data):
    library_name = str(data.get("library_name") or "").strip()
    query = str(data.get("query") or "").strip()
    if not library_name:
        return "(error: library_name required)"
    if not query:
        return "(error: query required)"

    api_key = _get_api_key(ctx.context.bot)
    try:
        results = await run_in_executor(
            _search_sync, library_name, query, api_key
        )
    except httpx.HTTPStatusError as e:
        return f"(error: Context7 API returned {e.response.status_code})"
    except (httpx.HTTPError, OSError) as e:
        return f"(error: Context7 request failed: {type(e).__name__})"

    return _format_search(results)


def _docs_sync(
    library_id: str, query: str, api_key: str | None
) -> DocsResponse:
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        r = client.get(
            _CONTEXT_URL,
            params={"libraryId": library_id, "query": query, "type": "json"},
            headers=_headers(api_key),
        )
        r.raise_for_status()
        return cast(DocsResponse, r.json())


def _format_docs(data: DocsResponse) -> str:
    parts = []

    for cs in data.get("codeSnippets") or []:
        title = cs.get("codeTitle") or ""
        lang = cs.get("codeLanguage") or ""
        desc = cs.get("codeDescription") or ""
        for block in cs.get("codeList") or []:
            code = block.get("code") or ""
            header = f"### {title}"
            if lang:
                header += f" ({lang})"
            if desc:
                header += f"\n{desc}"
            parts.append(f"{header}\n```{lang}\n{code}\n```")

    for info in data.get("infoSnippets") or []:
        breadcrumb = info.get("breadcrumb") or ""
        content = info.get("content") or ""
        header = f"### Docs: {breadcrumb}" if breadcrumb else "### Docs"
        parts.append(f"{header}\n{content}")

    rules = data.get("rules")
    rules_content = rules.get("content") if isinstance(rules, dict) else rules
    if rules_content:
        parts.insert(0, f"### Rules\n{rules_content}")

    if not parts:
        return "No documentation found for this query."

    text = "\n\n---\n\n".join(parts)
    if len(text) > _MAX_DOC_CHARS:
        text = text[:_MAX_DOC_CHARS] + "\n\n...(truncated)"
    return text


@tool(
    name="context7_docs",
    description=(
        "Fetch up-to-date documentation and code examples for a specific library. "
        "Requires the library's Context7 ID (get it from context7_search first, "
        "e.g. '/vercel/next.js'). Returns relevant code snippets and documentation "
        "passages ranked by relevance to your query."
    ),
    schema={
        "type": "object",
        "properties": {
            "library_id": {
                "type": "string",
                "description": "Context7 library ID (e.g. '/vercel/next.js', '/django/django'). "
                "Optionally pin version: '/vercel/next.js@v15.1.0'",
            },
            "query": {
                "type": "string",
                "description": "Specific question about the library — used for relevance ranking",
            },
        },
        "required": ["library_id", "query"],
    },
)
async def context7_docs(ctx, data):
    library_id = str(data.get("library_id") or "").strip()
    query = str(data.get("query") or "").strip()
    if not library_id:
        return "(error: library_id required)"
    if not query:
        return "(error: query required)"
    if not library_id.startswith("/"):
        library_id = "/" + library_id

    api_key = _get_api_key(ctx.context.bot)
    try:
        result = await run_in_executor(_docs_sync, library_id, query, api_key)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 404:
            return "(error: library not found — check the ID and try context7_search)"
        return f"(error: Context7 API returned {status})"
    except (httpx.HTTPError, OSError) as e:
        return f"(error: Context7 request failed: {type(e).__name__})"

    return _format_docs(result)
