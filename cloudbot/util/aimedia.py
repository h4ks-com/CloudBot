"""Thin client for an AI media-generation API (images + async video).

URL and key come from ``config.json`` under ``plugins.aimedia``; there is no
built-in default::

    "aimedia": {"api_url": "...", "api_key": "..."}

Images return inline (base64) and are re-uploaded by the plugin. Video is async:
``submit_video`` returns a job id, ``watch_video`` registers it, and a periodic
tick (``poll_watches``) posts the link once it renders.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from cloudbot.util.web import get_session

TIMEOUT = 90
EDIT_TIMEOUT = 180  # img2img edits run through a browser, so they take longer than plain gen
VIDEO_WATCH_TIMEOUT = (
    600.0  # renders take a few minutes; stop watching a stuck job past this
)

PostFn = Callable[[str, str, str], None]


class MediaGenError(Exception):
    """Any failure talking to the media-generation API."""


class MediaGenNotConfigured(MediaGenError):
    """The api_url or api_key is missing from config."""


def config_from_bot(bot: Any) -> tuple[str, str]:
    """Return ``(api_url, api_key)`` from ``plugins.aimedia`` config, or raise."""
    cfg = (bot.config.get("plugins") or {}).get("aimedia") or {}
    url = str(cfg.get("api_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    if not url or not key:
        raise MediaGenNotConfigured(
            "media generation not configured — set plugins.aimedia.api_url and api_key in config.json"
        )
    return url, key


def _request(
    method: str,
    url: str,
    key: str,
    path: str,
    timeout: int = TIMEOUT,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        resp = get_session().request(
            method,
            f"{url}{path}",
            headers={"X-API-Key": key},
            timeout=timeout,
            **kwargs,
        )
    except requests.RequestException as e:
        # timeout / connection drop — surface it instead of a silent no-reply
        raise MediaGenError(f"request failed: {e}") from e
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except ValueError:
            detail = resp.text[:200]
        raise MediaGenError(f"HTTP {resp.status_code}: {detail}") from e
    try:
        data: dict[str, Any] = resp.json() if resp.content else {}
    except ValueError as e:
        raise MediaGenError(f"invalid JSON response: {e}") from e
    return data


def generate_image(url: str, key: str, prompt: str) -> list[bytes]:
    """POST /image — text->image. Returns the decoded image variants."""
    data = _request("POST", url, key, "/image", json={"prompt": prompt})
    return [base64.b64decode(b) for b in data.get("images", [])]


def edit_image(url: str, key: str, prompt: str, images_b64: list[str]) -> list[bytes]:
    """POST /image with source image(s) — img2img edit. Returns the decoded result."""
    data = _request(
        "POST",
        url,
        key,
        "/image",
        timeout=EDIT_TIMEOUT,
        json={"prompt": prompt, "images_b64": images_b64},
    )
    return [base64.b64decode(b) for b in data.get("images", [])]


def submit_video(url: str, key: str, prompt: str, images_b64: list[str] | None = None) -> str:
    """POST /video — submit an async video render. Returns the job id.

    With *images_b64* the source image(s) are animated (image->video); without them, text->video.
    """
    payload: dict[str, Any] = {"prompt": prompt}
    if images_b64:
        payload["images_b64"] = images_b64
    data = _request("POST", url, key, "/video", json=payload)
    job_id = data.get("job_id")
    if not job_id:
        raise MediaGenError(f"no job_id in response: {data}")
    return str(job_id)


def get_video_job(url: str, key: str, job_id: str) -> dict[str, Any]:
    """GET /video/{id} — poll a render: {status, url, error, ...}."""
    return _request("GET", url, key, f"/video/{job_id}")


@dataclass
class _Watch:
    network: str
    chan: str
    nick: str
    api_url: str
    api_key: str
    job_id: str
    deadline: float


_watches: list[_Watch] = []


def watch_video(
    api_url: str,
    api_key: str,
    job_id: str,
    *,
    network: str,
    chan: str,
    nick: str,
) -> None:
    """Register a submitted job so poll_watches posts its url when it finishes."""
    _watches.append(
        _Watch(
            network,
            chan,
            nick,
            api_url,
            api_key,
            job_id,
            time.monotonic() + VIDEO_WATCH_TIMEOUT,
        )
    )


def poll_watches(post: PostFn) -> None:
    """Advance every pending watch once; ``post(network, chan, msg)`` delivers.

    Called from the plugin's periodic hook. A render in progress is left for the
    next tick; a complete/failed job (or one past the watch timeout) is posted and
    dropped.
    """
    for watch in list(_watches):
        expired = time.monotonic() > watch.deadline
        try:
            job = get_video_job(watch.api_url, watch.api_key, watch.job_id)
        except MediaGenError as e:
            if expired:
                post(
                    watch.network,
                    watch.chan,
                    f"{watch.nick}: video {watch.job_id} gave up — {e}",
                )
                _watches.remove(watch)
            continue
        status = job.get("status")
        if status == "complete":
            post(
                watch.network, watch.chan, f"{watch.nick}: 🎬 {job.get('url')}"
            )
        elif status == "failed":
            post(
                watch.network,
                watch.chan,
                f"{watch.nick}: ❌ video failed — {job.get('error') or 'unknown error'}",
            )
        elif expired:
            post(
                watch.network,
                watch.chan,
                f"{watch.nick}: ⏳ video {watch.job_id} still rendering — check later",
            )
        else:
            continue
        _watches.remove(watch)
