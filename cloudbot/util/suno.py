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
# /generate is synchronous on the server (browser flow through cloak): ~40-60s
# healthy, but a stale-session account can burn ~5.5 minutes before the pool
# rotates to the next one — the bound must survive one bad account plus a good
# attempt, or the clips get generated server-side and the clip ids are lost.
GENERATE_TIMEOUT = 420
# A clip's finished MP3 may lag the submit by a couple of minutes; covers run
# the full UI flow and take longer. Stop watching past these so a stuck job
# doesn't leak a pending entry forever.
TEXT_TIMEOUT = 360.0
COVER_TIMEOUT = 720.0
# Suno's own hosts are not linkable any more, so every public link comes from
# our API's /download, which mirrors the clip to our bucket and redirects there.


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
    method: str,
    url: str,
    key: str,
    path: str,
    timeout: float = TIMEOUT,
    **kwargs: Any,
) -> dict[str, Any]:
    resp = get_session().request(
        method,
        f"{url}{path}",
        headers={"X-API-Key": key},
        timeout=timeout,
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
    return _request(
        "POST", url, key, "/generate", timeout=GENERATE_TIMEOUT, json=body
    )


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
    return _request(
        "POST",
        url,
        key,
        "/generate/cover/url",
        timeout=GENERATE_TIMEOUT,
        json=body,
    )


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


def final_url(url: str, key: str, clip_id: str) -> str:
    """Where the API redirects this clip's audio to, or "" if it is unreachable."""
    try:
        resp = get_session().get(
            f"{url}/download/{clip_id}",
            headers={"X-API-Key": key},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        return ""
    location = resp.headers.get("Location", "")
    return location if resp.is_redirect else ""


def final_links(url: str, key: str, clip_ids: list[str]) -> str:
    """Joined public links for these clips."""
    return " | ".join(
        link for link in (final_url(url, key, i) for i in clip_ids) if link
    )


def extract_clip_ids(resp: dict[str, Any]) -> list[str]:
    """Clip ids present in a /generate or /jobs response."""
    return [c["id"] for c in (resp.get("clips") or []) if c.get("id")]


def clip_ready(url: str, key: str, clip_id: str) -> bool:
    """True once the clip has been mirrored and its link serves audio."""
    link = final_url(url, key, clip_id)
    if not link:
        return False
    try:
        resp = get_session().head(link, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return False
    return resp.status_code == 200


def format_generation(resp: dict[str, Any], url: str, key: str) -> str:
    """One-line summary of a /generate or /jobs response.

    A finished clip gets its bucket link; a rendering one is reported as
    rendering, and the watcher posts the link when it lands.
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
        links = final_links(url, key, ids)
        if links:
            return f"{_b('✅ complete')} → {links}"
    return f"🎵 {_b(status)} — rendering {len(ids)} clip(s), link(s) posted when ready"


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
    ``done`` drops the watch.
    """
    timed_out = time.time() > watch.deadline
    if watch.kind == "text":
        if all(clip_ready(watch.url, watch.key, i) for i in watch.clip_ids):
            links = final_links(watch.url, watch.key, watch.clip_ids)
            return f"{watch.nick}: {_b('✅ song ready')} → {links}", True
        if timed_out:
            return (
                f"{watch.nick}: ⏰ song still rendering — give it another minute",
                True,
            )
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
        links = final_links(watch.url, watch.key, extract_clip_ids(resp))
        return f"{watch.nick}: {_b('✅ cover ready')} → {links}", True
    if status == "failed":
        err = resp.get("error") or "unknown error"
        return f"{watch.nick}: {_b('❌ cover failed')}: {err}", True
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
    timeout: float = WAIT_TIMEOUT,
    interval: float = WAIT_INTERVAL,
) -> str:
    """Block until ``ident`` has playable audio and return its URL(s).

    Text lands in ~1-2min, a cover in ~6min. On timeout, says so; on a failed
    cover, returns the error.
    """
    job = _job_or_none(url, key, ident)
    is_job = job is not None
    deadline = time.time() + timeout
    while True:
        if is_job and job is not None:
            status = job.get("status")
            if status == "failed":
                return f"failed: {job.get('error') or 'unknown error'}"
            if status == "complete":
                return final_links(url, key, extract_clip_ids(job))
        elif clip_ready(url, key, ident):
            return final_url(url, key, ident)

        if time.time() > deadline:
            return f"(timeout) {ident} is still rendering"
        time.sleep(interval)
        if is_job:
            job = get_job(url, key, ident)
