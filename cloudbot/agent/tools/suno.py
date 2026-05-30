"""Suno music-generation tools (self-hosted Suno API).

Thin wrappers over ``cloudbot.util.suno`` — the same client the ``suno`` IRC
plugin uses — so request logic and formatting are shared. Config (api_url +
api_key) is read from ``plugins.suno`` via the bot on ``ctx.context.bot``.
"""

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import suno


def _config(ctx) -> tuple[str, str] | None:
    try:
        return suno.config_from_bot(ctx.context.bot)
    except suno.SunoNotConfigured:
        return None


def _reply_target(ctx) -> tuple[str, str, str]:
    """Channel, network, and nick of the request, for the completion watcher."""
    event = ctx.context
    conn = getattr(event, "conn", None)
    return (
        getattr(event, "chan", "") or "",
        getattr(conn, "name", "") or "",
        getattr(event, "nick", "") or "",
    )


@tool(
    name="suno_generate_song",
    description=(
        "Generate an original song from a text prompt using Suno AI. Returns clip "
        "ids and public CDN MP3 links (audio finishes rendering ~1-2 min after "
        "submission). Use for 'make a song about X' style requests."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Song description / style (e.g. 'upbeat synthwave about the ocean')",
            },
            "instrumental": {
                "type": "boolean",
                "description": "Generate without vocals/lyrics (default false)",
            },
            "lyrics": {
                "type": "string",
                "description": "Optional custom lyrics; empty lets Suno write them",
            },
            "title": {"type": "string", "description": "Optional song title"},
        },
        "required": ["prompt"],
    },
)
async def suno_generate_song(ctx, data):
    cfg = _config(ctx)
    if cfg is None:
        return "(error: Suno not configured)"
    url, key = cfg
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return "(error: prompt required)"
    try:
        resp = await run_in_executor(
            suno.generate_song,
            url,
            key,
            prompt,
            instrumental=bool(data.get("instrumental", False)),
            lyrics=str(data.get("lyrics") or ""),
            title=str(data.get("title") or ""),
        )
    except suno.SunoError as e:
        return f"(error: {e})"
    chan, network, nick = _reply_target(ctx)
    ids = suno.extract_clip_ids(resp)
    suno.watch_text(url, key, ids, chan=chan, network=network, nick=nick)
    summary = suno.format_generation(resp, url, key)
    if ids:
        summary += f" [ids: {','.join(ids)} — suno_wait_for_song to chain]"
    return summary


@tool(
    name="suno_cover_from_url",
    description=(
        "Generate a Suno cover/remix from a remote audio URL, optionally steered "
        "by a style prompt (e.g. cover this URL 'as epic orchestral'). Runs "
        "asynchronously (~6 min); returns a job id. Poll it later with suno_job_status."
    ),
    schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Public http(s) URL of the source audio to cover",
            },
            "prompt": {
                "type": "string",
                "description": "Optional style prompt (e.g. 'epic orchestral remix'); empty infers from the audio",
            },
            "instrumental": {
                "type": "boolean",
                "description": "Generate without vocals (default true)",
            },
        },
        "required": ["url"],
    },
)
async def suno_cover_from_url(ctx, data):
    cfg = _config(ctx)
    if cfg is None:
        return "(error: Suno not configured)"
    url, key = cfg
    audio_url = str(data.get("url") or "").strip()
    if not audio_url.startswith(("http://", "https://")):
        return "(error: url must start with http:// or https://)"
    try:
        resp = await run_in_executor(
            suno.cover_from_url,
            url,
            key,
            audio_url,
            prompt=str(data.get("prompt") or ""),
            instrumental=bool(data.get("instrumental", True)),
            wait=False,
        )
    except suno.SunoError as e:
        return f"(error: {e})"
    job_id = str(resp.get("id", ""))
    chan, network, nick = _reply_target(ctx)
    suno.watch_cover(url, key, job_id, chan=chan, network=network, nick=nick)
    return (
        f"cover submitted: job {job_id or '?'} — posted to channel when ready; "
        "suno_wait_for_song to chain on the result"
    )


@tool(
    name="suno_wait_for_song",
    description=(
        "Block until a song/cover is ready and return its URL, so you can chain "
        "on the result (e.g. then hand the file to another tool). Pass a clip id "
        "(from suno_generate_song) or a job id (from suno_cover_from_url). "
        "mode='stream' returns a playable live URL fast (text instant, cover "
        "~80s); mode='final' waits for the finished CDN mp3 (text ~1-2min, cover "
        "~6min). Only use when a later step needs the audio — plain 'make a song' "
        "requests don't need it."
    ),
    schema={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Clip id (text) or job id (cover) to wait on",
            },
            "mode": {
                "type": "string",
                "enum": ["stream", "final"],
                "description": "'stream' = fast live URL; 'final' = finished mp3 (slower)",
            },
        },
        "required": ["id"],
    },
)
async def suno_wait_for_song(ctx, data):
    cfg = _config(ctx)
    if cfg is None:
        return "(error: Suno not configured)"
    url, key = cfg
    ident = str(data.get("id") or "").strip()
    if not ident:
        return "(error: id required)"
    mode = "stream" if data.get("mode") == "stream" else "final"
    try:
        return await run_in_executor(
            suno.wait_for_song, url, key, ident, mode=mode
        )
    except suno.SunoError as e:
        return f"(error: {e})"


@tool(
    name="suno_job_status",
    description="Check an async Suno cover job by id; returns clip CDN links when complete.",
    schema={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Job id from suno_cover_from_url",
            }
        },
        "required": ["job_id"],
    },
)
async def suno_job_status(ctx, data):
    cfg = _config(ctx)
    if cfg is None:
        return "(error: Suno not configured)"
    url, key = cfg
    job_id = str(data.get("job_id") or "").strip()
    if not job_id:
        return "(error: job_id required)"
    try:
        resp = await run_in_executor(suno.get_job, url, key, job_id)
    except suno.SunoError as e:
        return f"(error: {e})"
    return suno.format_generation(resp, url, key)


@tool(
    name="suno_credits",
    description="Show remaining Suno generation credits across all accounts.",
    schema={"type": "object", "properties": {}},
)
async def suno_credits(ctx, _data):
    cfg = _config(ctx)
    if cfg is None:
        return "(error: Suno not configured)"
    url, key = cfg
    try:
        resp = await run_in_executor(suno.get_credits, url, key)
    except suno.SunoError as e:
        return f"(error: {e})"
    return suno.format_credits(resp)
