"""Tools for managing games hosted at games.h4ks.com.

`vibegame_upload` and `vibegame_search` use the games.h4ks.com REST API.
`vibegame_import_url` deliberately bypasses that API and pushes to the
backing GitHub repo directly because the platform's PUT endpoint rejects
binary base64 with "Invalid base64 content" — pushing via the GitHub
Contents API works because the platform serves anything in the repo.
"""

import base64
import logging
import re
from typing import Any, Optional

import requests

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool

logger = logging.getLogger("cloudbot")

_VIBEDGAMES_REPO = "h4ks-com/vibedgames-ai"


def vibegame_url(project: str) -> str:
    """Public subdomain URL for a vibegame project."""
    return f"https://{project}.games.h4ks.com/"


async def _vibegame_request(
    bot,
    method: str,
    path: str,
    *,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> tuple[int, Any]:
    """Authenticated request to the vibegames API. Returns (status, body)."""
    api_url = bot.config.get_api_key("vibegames_api_url") or ""
    api_key = bot.config.get_api_key("vibegames_api_key") or ""
    if not api_url or not api_key:
        return (
            0,
            "(error: vibegames_api_url or vibegames_api_key not configured)",
        )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = await run_in_executor(
            requests.request,
            method,
            f"{api_url}{path}",
            headers=headers,
            json=json_body,
            params=params,
            timeout=30,
        )
    except requests.RequestException as e:
        return 0, f"(error: vibegames request failed: {e})"
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text[:2000]


@tool(
    name="vibegame_upload",
    description=(
        "Persist a game / asset to the public games.h4ks.com platform. "
        "Use ONLY when the user explicitly asks to 'persist', 'add to games.h4ks.com', "
        "'add to the games platform', 'save the game permanently', or similar. "
        "First call with path='index.html' and full HTML content to CREATE the game — "
        "this returns the live URL https://<project>.games.h4ks.com/. "
        "Subsequent calls with path='styles.css' / 'script.js' / 'assets/sprite.png' add files. "
        "For binary files (images, audio) base64-encode the content and set encoding='base64'. "
        "Project name must be lowercase a-z0-9_- (no spaces, no caps). "
        "Files commit directly to h4ks-com/vibedgames-ai — no fork/PR needed."
    ),
    schema={
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project slug (lowercase, [a-z0-9_-])",
            },
            "path": {
                "type": "string",
                "description": "File path within project (default: 'index.html')",
            },
            "content": {
                "type": "string",
                "description": "File content (text or base64 if encoding='base64')",
            },
            "encoding": {
                "type": "string",
                "enum": ["base64"],
                "description": "Set 'base64' for binary",
            },
        },
        "required": ["project", "content"],
    },
)
async def vibegame_upload(ctx, data):
    project = str(data.get("project") or "").strip().lower()
    path = str(data.get("path") or "index.html").strip().lstrip("/")
    content = str(data.get("content") or "")
    encoding = str(data.get("encoding") or "").strip().lower()
    if not project or not content:
        return "(error: project and content required)"
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", project):
        return "(error: project name must be lowercase a-z0-9 + _-, must start with alphanumeric)"
    body: dict[str, Any] = {"content": content}
    if encoding == "base64":
        body["encoding"] = "base64"
    status, payload = await _vibegame_request(
        ctx.context.bot,
        "PUT",
        f"/api/project/{project}/{path}",
        json_body=body,
    )
    if status == 0:
        return str(payload)
    if status >= 400:
        msg = (
            payload.get("message")
            if isinstance(payload, dict)
            else str(payload)
        )
        return f"(error: vibegames upload failed status={status} message={msg})"
    url = vibegame_url(project)
    if path == "index.html":
        return f"Uploaded — game live at {url}\n(github: https://github.com/h4ks-com/vibedgames-ai/blob/main/games/{project}/index.html)"
    file_url = f"{url}{path}"
    return f"Uploaded {path} — accessible at {file_url}"


@tool(
    name="vibegame_import_url",
    description=(
        "Download a remote URL (image, audio, video, json, etc.) and add it as a file in an "
        "EXISTING games.h4ks.com project. Use AFTER vibegame_upload created the project. "
        "Pushes binary directly to h4ks-com/vibedgames-ai via GitHub Contents API (the games "
        "platform API can't accept binary base64 — this is the working path). "
        "Common flow: 1) .plimage / .plaudio generates an asset URL, "
        "2) vibegame_upload creates the project with index.html referencing <img src='cover.png'>, "
        "3) vibegame_import_url(project, 'cover.png', <generated URL>) — game's relative <img> "
        "now resolves to the just-uploaded asset. "
        "Max 10MB. Project name [a-z0-9_-] only."
    ),
    schema={
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Existing project slug",
            },
            "path": {
                "type": "string",
                "description": "Destination filename, e.g. 'cover.png', 'bg.mp3', 'data.json'",
            },
            "url": {
                "type": "string",
                "description": "Source URL to download (http/https)",
            },
        },
        "required": ["project", "path", "url"],
    },
)
async def vibegame_import_url(ctx, data):
    project = str(data.get("project") or "").strip().lower()
    path = str(data.get("path") or "").strip().lstrip("/")
    url = str(data.get("url") or "").strip()
    if not project or not path or not url:
        return "(error: project, path, and url required)"
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", project):
        return "(error: project name must be lowercase a-z0-9 + _-)"
    if not url.startswith(("http://", "https://")):
        return "(error: url must be http(s)://)"
    try:
        resp = await run_in_executor(requests.get, url, timeout=30, stream=True)
        resp.raise_for_status()
        content = resp.content
    except requests.RequestException as e:
        return f"(error fetching {url}: {e})"
    if len(content) > 10 * 1024 * 1024:
        return f"(error: file too large {len(content)} bytes, max 10MB)"
    # Use GitHub Contents API directly — games.h4ks.com's PUT /api/project/.../<path>
    # rejects binary base64 with "Invalid base64 content" so we git-push and the
    # next /game/<project>/<path> request serves it from the repo automatically.
    b64 = base64.b64encode(content).decode("ascii")
    bot = ctx.context.bot
    token = bot.config.get_api_key("github") or ""
    if not token:
        return "(error: no github PAT configured for vibegame_import_url)"
    owner, repo = _VIBEDGAMES_REPO.split("/")
    gh_path = f"games/{project}/{path}"
    gh_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{gh_path}"
    gh_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    # Existing files require a SHA on PUT — fetch first.
    async def fetch_sha() -> Optional[str]:
        try:
            r = await run_in_executor(
                requests.get,
                gh_url,
                headers=gh_headers,
                timeout=10,
            )
            if r.status_code == 200:
                return r.json().get("sha")
        except requests.RequestException:
            pass
        return None

    sha = await fetch_sha()
    body: dict[str, Any] = {
        "message": f"vibegame_import_url: upload {path} to {project}",
        "content": b64,
    }
    if sha:
        body["sha"] = sha
    try:
        r = await run_in_executor(
            requests.put,
            gh_url,
            headers={**gh_headers, "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
    except requests.RequestException as e:
        return f"(error pushing to github: {e})"
    if r.status_code not in (200, 201):
        return f"(error: github push failed status={r.status_code} body={r.text[:200]})"
    # Asset is served from games.h4ks.com/game/<project>/<path>. The subdomain
    # version (<project>.games.h4ks.com/<path>) DOES NOT serve sub-paths — it
    # falls back to index.html. So instruct agent to use relative paths inside
    # the index.html (which is opened from /game/<project>/, where relatives work).
    relative_url = f"https://games.h4ks.com/game/{project}/{path}"
    return (
        f"Pushed {len(content)} bytes from {url} → games/{project}/{path}\n"
        f"Asset is now part of the project. Reference from index.html using "
        f"relative path: <img src='{path}'> / <audio src='{path}'>.\n"
        f"Direct URL (absolute): {relative_url}"
    )


@tool(
    name="vibegame_search",
    description=(
        "Search existing games on games.h4ks.com. Use to check if a project name is taken "
        "before vibegame_upload (project names must be unique)."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (project name or keyword)",
            },
        },
        "required": ["query"],
    },
)
async def vibegame_search(ctx, data):
    query = str(data.get("query") or "").strip()
    if not query:
        return "(error: query required)"
    status, payload = await _vibegame_request(
        ctx.context.bot,
        "GET",
        "/api/games",
        params={"search_query": query, "sort_by": "hottest"},
    )
    if status == 0:
        return str(payload)
    if status >= 400 or not isinstance(payload, list):
        return f"(error: search failed status={status})"
    if not payload:
        return f"(no games match '{query}')"
    lines = []
    for r in payload[:5]:
        if not isinstance(r, dict):
            continue
        url = r.get("subdomain_url") or r.get("path_url") or ""
        lines.append(f"{r.get('project')}: {url} ({r.get('num_opens')} opens)")
    return "\n".join(lines)
