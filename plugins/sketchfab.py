"""Sketchfab 3D model search and interactive Three.js viewer.

Commands:
  .sk <query>   search for free downloadable 3D models
  .skn          next search result
"""

import logging
import re
from pathlib import Path

import requests

from cloudbot import hook
from cloudbot.agent.sketchfab_client import download_model, search
from cloudbot.util import web

logger = logging.getLogger("cloudbot")

_VIEWER_TEMPLATE = Path(__file__).parent / "sketchfab_viewer.html"

_user_results: dict[str, list[dict]] = {}


def _clean_paste_url(raw: str) -> str:
    """Extract the URL from paste responses like 'File already exists: https://...'."""
    m = re.search(r"(https?://\S+)", raw)
    return m.group(1) if m else raw


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_viewer_html(model_url: str, model: dict) -> str:
    meta = (
        f"by {_esc(model['user'])} \u00b7 "
        f"Faces: {model['faces']} \u00b7 Verts: {model['verts']} \u00b7 "
        f"License: {_esc(model['license'])}<br>"
        f'<a href="{model["url"]}" target="_blank">View on Sketchfab</a>'
    )
    template = _VIEWER_TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("__URL__", model_url)
        .replace("__NAME__", _esc(model["name"]))
        .replace("__META__", meta)
    )


def _process_result(api_key: str, model: dict) -> str:
    name = model["name"]

    try:
        data, fmt = download_model(api_key, model["uid"])
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code in (400, 403):
            return f"\x02{name}\x02 - download not available"
        return f"\x02{name}\x02 - download error ({code})"
    except ValueError as e:
        return f"\x02{name}\x02 - {e}"
    except requests.RequestException as e:
        return f"\x02{name}\x02 - download failed: {e}"

    ext = "glb" if fmt == "glb" else "zip"
    try:
        model_url = _clean_paste_url(web.paste(data, ext=ext))
    except OSError as e:
        return f"\x02{name}\x02 - upload failed: {e}"

    if fmt != "glb":
        return (
            f"\x02{name}\x02 by {model['user']} "
            f"({model['faces']} faces) - "
            f"Download: {model_url} - Sketchfab: {model['url']}"
        )

    try:
        viewer_html = _build_viewer_html(model_url, model)
        viewer_url = _clean_paste_url(web.paste(viewer_html.encode("utf-8"), ext="html"))
    except OSError as e:
        return (
            f"\x02{name}\x02 - viewer upload failed: {e} | Model: {model_url}"
        )

    return (
        f"\x02{name}\x02 by {model['user']} "
        f"({model['faces']} faces, {model['verts']} verts) "
        f"- \x02Viewer\x02: {viewer_url} - Model: {model_url}"
    )


@hook.command("sketchfab", "sk", autohelp=False)
def sketchfab_search(text: str, nick: str, bot, reply) -> str:
    """<query> - Search Sketchfab for free downloadable 3D models"""
    api_key = bot.config.get_api_key("sketchfab") or ""
    if not api_key:
        return "Sketchfab API key not configured."
    if not text.strip():
        return "Usage: .sk <query>"

    try:
        results = search(api_key, text.strip())
    except requests.RequestException as e:
        return f"Search error: {e}"

    if not results:
        return "No downloadable models found."

    _user_results[nick] = results
    return _process_result(api_key, results[0])


@hook.command("sketchfabn", "skn", autohelp=False)
def sketchfab_next(nick: str, bot, reply) -> str:
    """- Next Sketchfab search result"""
    api_key = bot.config.get_api_key("sketchfab") or ""
    if not api_key:
        return "Sketchfab API key not configured."

    queue = _user_results.get(nick)
    if not queue:
        return "No more results. Use .sk <query> to search."
    queue.pop(0)
    if not queue:
        return "No more results."

    return _process_result(api_key, queue[0])
