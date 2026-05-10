"""Sketchfab agent tools — search and download 3D models as raw GLB files."""

import re

import requests

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.agent.sketchfab_client import (
    download_model,
    search,
)
from cloudbot.util import web


def _clean_paste_url(raw: str) -> str:
    m = re.search(r"(https?://\S+)", raw)
    return m.group(1) if m else raw


@tool(
    name="sketchfab_search",
    description=(
        "Search Sketchfab for free downloadable 3D models. "
        "Returns model names, UIDs, face/vertex counts, and license info. "
        "Use sketchfab_download with a UID to get the model file."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (e.g., 'car', 'tree', 'character')",
            },
        },
        "required": ["query"],
    },
)
async def sketchfab_search(ctx, data) -> str:
    event = ctx.context
    api_key = event.bot.config.get_api_key("sketchfab") or ""
    if not api_key:
        return "(error: sketchfab API key not configured)"

    query = str(data.get("query", "")).strip()
    if not query:
        return "(error: query required)"

    try:
        results = await run_in_executor(search, api_key, query, 5)
    except requests.RequestException as e:
        return f"(error searching sketchfab: {e})"

    if not results:
        return "No downloadable models found."

    lines = []
    for i, m in enumerate(results, 1):
        lines.append(
            f"{i}. {m['name']} (uid:{m['uid']}) "
            f"by {m['user']} - "
            f"{m['faces']} faces, {m['verts']} verts, "
            f"license: {m['license']}"
        )
    return "\n".join(lines)


@tool(
    name="sketchfab_download",
    description=(
        "Download a Sketchfab 3D model as GLB and upload it to the paste service. "
        "Returns the download URL. Use sketchfab_search first to find model UIDs."
    ),
    schema={
        "type": "object",
        "properties": {
            "uid": {
                "type": "string",
                "description": "Sketchfab model UID (from sketchfab_search results)",
            },
        },
        "required": ["uid"],
    },
)
async def sketchfab_download(ctx, data) -> str:
    event = ctx.context
    api_key = event.bot.config.get_api_key("sketchfab") or ""
    if not api_key:
        return "(error: sketchfab API key not configured)"

    uid = str(data.get("uid", "")).strip()
    if not uid:
        return "(error: uid required)"

    try:
        model_data, fmt = await run_in_executor(download_model, api_key, uid)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code in (400, 403):
            return "(error: download not available for this model)"
        return f"(error getting download link: {e})"
    except ValueError as e:
        return f"(error: {e})"
    except requests.RequestException as e:
        return f"(error downloading model: {e})"

    ext = "glb" if fmt == "glb" else "zip"
    try:
        url = _clean_paste_url(await run_in_executor(web.paste, model_data, ext))
    except (OSError, ValueError) as e:
        return f"(error uploading model: {e})"

    label = "GLB" if fmt == "glb" else "GLTF (zip)"
    return f"Model uploaded ({label}): {url}"
