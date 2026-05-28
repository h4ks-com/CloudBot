"""Suno music-generation IRC commands + completion watcher.

Thin command layer over ``cloudbot.util.suno`` — the same shared client the
agent's Suno tools use — so request logic and formatting stay in one place.
Submits ack immediately with the live-stream link; a periodic watcher posts
the finished MP3 link to the channel once it renders, so nobody has to poll
``.sunojob`` by hand.

Config (``config.json``)::

    "plugins": {"suno": {"api_url": "...", "api_key": "..."}}
"""

from cloudbot import hook
from cloudbot.util import suno


def _client(bot):
    """Return ``(api_url, api_key)`` or an error string to send to the channel."""
    try:
        return suno.config_from_bot(bot)
    except suno.SunoNotConfigured as e:
        return str(e)


def _channel_only(chan):
    """Error string if used outside a channel (PMs have chan == sender nick)."""
    if not chan or not chan.startswith("#"):
        return "🎵 suno commands work in channels only, not PMs"
    return None


@hook.command("suno", autohelp=False)
def suno_generate(text, bot, chan, nick, conn):
    """<prompt> - generate a song from a text prompt with Suno AI."""
    pm = _channel_only(chan)
    if pm:
        return pm
    prompt = (text or "").strip()
    if not prompt:
        return "usage: .suno <prompt>"
    cfg = _client(bot)
    if isinstance(cfg, str):
        return cfg
    url, key = cfg
    try:
        resp = suno.generate_song(url, key, prompt)
    except suno.SunoError as e:
        return f"suno error: {e}"
    suno.watch_text(
        url,
        key,
        suno.extract_clip_ids(resp),
        chan=chan,
        network=conn.name,
        nick=nick,
    )
    return suno.format_generation(resp)


@hook.command("sunocover", autohelp=False)
def suno_cover(text, bot, chan, nick, conn):
    """<audio_url> [style prompt] - cover a remote audio URL (async)."""
    pm = _channel_only(chan)
    if pm:
        return pm
    audio_url, prompt = suno.split_audio_prompt(text)
    if not audio_url:
        return "usage: .sunocover <http(s) audio url> [style prompt]"
    cfg = _client(bot)
    if isinstance(cfg, str):
        return cfg
    url, key = cfg
    try:
        resp = suno.cover_from_url(
            url, key, audio_url, prompt=prompt, wait=False
        )
    except suno.SunoError as e:
        return f"suno error: {e}"
    job_id = resp.get("id", "")
    suno.watch_cover(url, key, job_id, chan=chan, network=conn.name, nick=nick)
    return (
        f"🎚️ cover {suno.BOLD}{job_id or '?'}{suno.BOLD} submitted — "
        "posting the live stream, then the final link, here"
    )


@hook.command("sunojob", autohelp=False)
def suno_job(text, bot, chan):
    """<job_id> - check an async Suno cover job."""
    pm = _channel_only(chan)
    if pm:
        return pm
    job_id = (text or "").strip().split()[0] if text else ""
    if not job_id:
        return "usage: .sunojob <job_id>"
    cfg = _client(bot)
    if isinstance(cfg, str):
        return cfg
    url, key = cfg
    try:
        resp = suno.get_job(url, key, job_id)
    except suno.SunoError as e:
        return f"suno error: {e}"
    return suno.format_generation(resp)


@hook.command("sunocredits", "sunostatus", autohelp=False)
def suno_credits(bot, chan):
    """- show remaining Suno credits across all accounts."""
    pm = _channel_only(chan)
    if pm:
        return pm
    cfg = _client(bot)
    if isinstance(cfg, str):
        return cfg
    url, key = cfg
    try:
        resp = suno.get_credits(url, key)
    except suno.SunoError as e:
        return f"suno error: {e}"
    return suno.format_credits(resp)


@hook.periodic(15, initial_interval=15)
def suno_watch_tick(bot):
    """Post finished song/cover links for any submit that has rendered."""

    def post(network, chan, message):
        conn = bot.connections.get(network)
        if conn and conn.ready:
            conn.message(chan, message)

    suno.poll_watches(post)
