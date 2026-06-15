"""Shared client for the self-hosted Suno music-generation API.

Used by both the IRC plugin (``plugins/suno.py``) and the agent tools
(``cloudbot/agent/tools/suno.py``) so request logic, error handling, and
result formatting live in exactly one place.

Both the API URL and key are read from ``config.json`` under ``plugins.suno``;
there is no built-in default — if either is unset the client fails::

    "suno": {"api_url": "...", "api_key": "..."}
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

from cloudbot.util.web import get_session

TIMEOUT = 90
# A clip's finished MP3 may lag the submit by a couple of minutes; covers run
# the full UI flow and take longer. Stop watching past these so a stuck job
# doesn't leak a pending entry forever.
TEXT_TIMEOUT = 360.0
COVER_TIMEOUT = 720.0
# Suno's public CDN — the finished MP3 for any clip id, no auth required.
CDN_TEMPLATE = "https://cdn1.suno.ai/{clip_id}.mp3"
# Suno's public live audio pipe — streams the clip progressively while it is
# still rendering (empty once finished; switch to the CDN then). No auth.
STREAM_TEMPLATE = "https://audiopipe.suno.ai/?item_id={clip_id}"


class SunoError(Exception):
    """Any failure talking to the Suno API."""


class SunoNotConfigured(SunoError):
    """The api_url or api_key is missing from config."""


def config_from_bot(bot: Any) -> tuple[str, str]:
    """Return ``(api_url, api_key)`` from ``plugins.suno`` config, or raise.

    No defaults: both must be set explicitly in config.json.
    """
    cfg = (bot.config.get("plugins") or {}).get("suno") or {}
    url = str(cfg.get("api_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    if not url or not key:
        raise SunoNotConfigured(
            "Suno not configured — set plugins.suno.api_url and api_key in config.json"
        )
    return url, key


def _request(
    method: str, url: str, key: str, path: str, **kwargs: Any
) -> dict[str, Any]:
    resp = get_session().request(
        method,
        f"{url}{path}",
        headers={"X-API-Key": key},
        timeout=TIMEOUT,
        **kwargs,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except ValueError:
            detail = resp.text[:200]
        raise SunoError(f"HTTP {resp.status_code}: {detail}") from e
    data: dict[str, Any] = resp.json() if resp.content else {}
    return data


# ── API calls ────────────────────────────────────────────────────────────────


def generate_song(
    url: str,
    key: str,
    prompt: str,
    *,
    instrumental: bool = False,
    lyrics: str = "",
    title: str = "",
) -> dict[str, Any]:
    """POST /generate — text-to-song. Returns submitted clips."""
    body = {
        "prompt": prompt,
        "instrumental": instrumental,
        "lyrics": lyrics,
        "title": title,
    }
    return _request("POST", url, key, "/generate", json=body)


def split_audio_prompt(text: str) -> tuple[str, str]:
    """Split free text into ``(audio_url, prompt)``.

    The first http(s) token is the audio to cover; everything else (in order)
    becomes the style prompt, so ``.sunocover <url> epic orchestral remix``
    covers the audio in that style.
    """
    audio_url = ""
    rest: list[str] = []
    for token in (text or "").split():
        if not audio_url and token.startswith(("http://", "https://")):
            audio_url = token
        else:
            rest.append(token)
    return audio_url, " ".join(rest)


def cover_from_url(
    url: str,
    key: str,
    audio_url: str,
    *,
    prompt: str = "",
    instrumental: bool = True,
    account: str | None = None,
    wait: bool = False,
) -> dict[str, Any]:
    """POST /generate/cover/url — cover a remote audio file. Async by default.

    ``prompt`` steers the cover's style; empty lets Suno infer it from the audio.
    """
    body: dict[str, Any] = {
        "url": audio_url,
        "instrumental": instrumental,
        "wait": wait,
    }
    if prompt:
        body["prompt"] = prompt
    if account:
        body["account"] = account
    return _request("POST", url, key, "/generate/cover/url", json=body)


def get_job(url: str, key: str, job_id: str) -> dict[str, Any]:
    """GET /jobs/{id} — poll an async cover job."""
    return _request("GET", url, key, f"/jobs/{job_id}")


def get_credits(url: str, key: str) -> dict[str, Any]:
    """GET /credits — aggregated per-account credits."""
    return _request("GET", url, key, "/credits")


# ── Formatting (shared IRC + agent output) ──────────────────────────────────

BOLD = "\x02"  # IRC bold toggle


def _b(text: str) -> str:
    """Wrap text in IRC bold."""
    return f"{BOLD}{text}{BOLD}"


def clip_cdn_url(clip_id: str) -> str:
    """Public CDN URL for a finished clip (works in a browser, no auth)."""
    return CDN_TEMPLATE.format(clip_id=clip_id)


def clip_stream_url(clip_id: str) -> str:
    """Public live-stream URL — plays the clip while it is still rendering."""
    return STREAM_TEMPLATE.format(clip_id=clip_id)


def final_url(url: str, key: str, clip_id: str) -> str:
    """Public bucket URL for a finished clip. The API's /download mirrors the
    clip to the bucket on first hit and 302s there, so this both stores it and
    returns the stable link; falls back to Suno's CDN if the API is unreachable.
    """
    try:
        resp = get_session().get(
            f"{url}/download/{clip_id}",
            headers={"X-API-Key": key},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        return clip_cdn_url(clip_id)
    location = resp.headers.get("Location", "")
    return location if resp.is_redirect and location else clip_cdn_url(clip_id)


def extract_clip_ids(resp: dict[str, Any]) -> list[str]:
    """Clip ids present in a /generate or /jobs response."""
    return [c["id"] for c in (resp.get("clips") or []) if c.get("id")]


def clip_ready(clip_id: str) -> bool:
    """True once the finished MP3 is live on the CDN.

    The CDN serves a 403 XML stub while a clip is still rendering and the real
    audio (200) once it lands, so a HEAD is enough to tell them apart.
    """
    try:
        resp = get_session().head(
            clip_cdn_url(clip_id), timeout=15, allow_redirects=True
        )
    except requests.RequestException:
        return False
    return resp.status_code == 200


def format_generation(resp: dict[str, Any], url: str, key: str) -> str:
    """One-line summary of a /generate or /jobs response.

    Once we have clip ids we return links immediately — no waiting for full
    render. While the clip is still generating we hand back the live stream
    URL; once complete we hand back the finished file from the bucket.
    """
    status = resp.get("status", "?")
    error = resp.get("error")
    ids = extract_clip_ids(resp)
    if status == "failed" or (error and not ids):
        return f"{_b('❌ failed')}: {error or 'unknown error'}"
    if not ids:
        job_id = resp.get("id", "?")
        return f"⏳ {status} — job {_b(job_id)} still rendering (.sunojob {job_id})"
    if status == "complete":
        links = " | ".join(final_url(url, key, i) for i in ids)
        return f"{_b('✅ complete')} → {links}"
    links = " | ".join(clip_stream_url(i) for i in ids)
    return f"🎵 {_b(status)} — ▶ live: {links}"


def format_credits(resp: dict[str, Any]) -> str:
    """Total Suno credits remaining across all accounts."""
    return f"🎵 {_b(str(resp.get('total', 0)))} Suno credits left"


# ── Completion watcher ───────────────────────────────────────────────────────
# A submit returns immediately (clips still rendering). Rather than make a user
# poll by hand, callers register the submitted job here; a single periodic tick
# (driven by the IRC plugin) posts the finished MP3 link once it lands. Text
# songs are watched via the public CDN; covers via the API's own job endpoint.

PostFn = Callable[[str, str, str], None]


@dataclass
class _Watch:
    kind: str  # "text" | "cover"
    url: str
    key: str
    chan: str
    network: str
    nick: str
    deadline: float
    clip_ids: list[str] = field(default_factory=list)
    job_id: str = ""
    stream_sent: bool = False


_watches: list[_Watch] = []


def watch_text(
    url: str,
    key: str,
    clip_ids: list[str],
    *,
    chan: str,
    network: str,
    nick: str,
) -> None:
    """Watch a text-gen song; its CDN links get posted to chan once rendered."""
    if clip_ids and chan and network:
        _watches.append(
            _Watch(
                kind="text",
                url=url,
                key=key,
                chan=chan,
                network=network,
                nick=nick,
                deadline=time.time() + TEXT_TIMEOUT,
                clip_ids=list(clip_ids),
            )
        )


def watch_cover(
    url: str,
    key: str,
    job_id: str,
    *,
    chan: str,
    network: str,
    nick: str,
) -> None:
    """Watch an async cover job; its result gets posted to chan once complete."""
    if job_id and chan and network:
        _watches.append(
            _Watch(
                kind="cover",
                url=url,
                key=key,
                chan=chan,
                network=network,
                nick=nick,
                deadline=time.time() + COVER_TIMEOUT,
                job_id=job_id,
            )
        )


def _tick(watch: _Watch) -> tuple[str | None, bool]:
    """Advance one watch.

    Returns ``(message, done)`` — ``message`` is posted to the channel when set,
    ``done`` drops the watch. A cover emits two messages over its lifetime (a
    live-stream link, then the finished file), so a posted message does not
    always finish the watch.
    """
    timed_out = time.time() > watch.deadline
    if watch.kind == "text":
        if all(clip_ready(i) for i in watch.clip_ids):
            links = " | ".join(
                final_url(watch.url, watch.key, i) for i in watch.clip_ids
            )
            return f"{watch.nick}: {_b('✅ song ready')} → {links}", True
        if timed_out:
            cdn = " | ".join(clip_cdn_url(i) for i in watch.clip_ids)
            return f"{watch.nick}: ⏰ still rendering — try {cdn} shortly", True
        return None, False
    try:
        resp = get_job(watch.url, watch.key, watch.job_id)
    except SunoError:
        if timed_out:
            return (
                f"{watch.nick}: ⏰ cover {_b(watch.job_id)} unreachable",
                True,
            )
        return None, False
    status = resp.get("status")
    if status == "complete":
        links = " | ".join(
            final_url(watch.url, watch.key, i) for i in extract_clip_ids(resp)
        )
        return f"{watch.nick}: {_b('✅ cover ready')} → {links}", True
    if status == "failed":
        err = resp.get("error") or "unknown error"
        return f"{watch.nick}: {_b('❌ cover failed')}: {err}", True
    ids = extract_clip_ids(resp)
    if ids and not watch.stream_sent:
        watch.stream_sent = True
        stream = " | ".join(clip_stream_url(i) for i in ids)
        return (
            f"{watch.nick}: 🎚️ cover {_b(watch.job_id)} → ▶ live: {stream}",
            False,
        )
    if timed_out:
        return (
            f"{watch.nick}: ⏰ cover {_b(watch.job_id)} still rendering",
            True,
        )
    return None, False


def poll_watches(post: PostFn) -> None:
    """Advance every pending watch once; ``post(network, chan, msg)`` delivers.

    Called from the IRC plugin's periodic hook, which supplies the post
    callback bound to the live connection.
    """
    for watch in list(_watches):
        message, done = _tick(watch)
        if message is not None:
            post(watch.network, watch.chan, message)
        if done:
            _watches.remove(watch)


# ── Blocking wait (agent chaining) ───────────────────────────────────────────
# Lets the agent generate a song and block until it has a usable URL, so the
# result can feed a next step. Text clips have no /jobs entry (resolved via the
# CDN directly); covers are tracked as jobs.

WAIT_TIMEOUT = 700.0  # under the agent's 900s per-run budget
WAIT_INTERVAL = 6.0


def _job_or_none(url: str, key: str, ident: str) -> dict[str, Any] | None:
    """Return the job for ``ident`` if it is a cover job, else None (a clip id)."""
    try:
        return get_job(url, key, ident)
    except SunoError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def wait_for_song(
    url: str,
    key: str,
    ident: str,
    *,
    mode: str = "final",
    timeout: float = WAIT_TIMEOUT,
    interval: float = WAIT_INTERVAL,
) -> str:
    """Block until ``ident`` reaches ``mode`` and return its URL(s).

    ``mode='stream'`` returns the live audiopipe URL as soon as clips exist
    (text: instant; cover: ~80s). ``mode='final'`` polls until the finished CDN
    mp3 lands (text: ~1-2min; cover: ~6min). On timeout, returns whatever URL is
    available with a note; on a failed cover, returns the error.
    """
    job = _job_or_none(url, key, ident)
    is_job = job is not None
    deadline = time.time() + timeout
    while True:
        if is_job and job is not None:
            status = job.get("status")
            ids = extract_clip_ids(job)
            if status == "failed":
                return f"failed: {job.get('error') or 'unknown error'}"
            if status == "complete":
                return " | ".join(final_url(url, key, i) for i in ids)
            if mode == "stream" and ids:
                return " | ".join(clip_stream_url(i) for i in ids)
        elif mode == "stream":
            return clip_stream_url(ident)
        elif clip_ready(ident):
            return final_url(url, key, ident)

        if time.time() > deadline:
            if is_job:
                live_ids = extract_clip_ids(job) if job is not None else []
                if live_ids:
                    live = " | ".join(clip_stream_url(i) for i in live_ids)
                    return f"(timeout, still rendering) {live}"
                return f"(timeout) job {ident} not ready yet"
            return f"(timeout) {clip_stream_url(ident)}"
        time.sleep(interval)
        if is_job:
            job = get_job(url, key, ident)
