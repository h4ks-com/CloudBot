import base64
import tempfile
from typing import Deque

from requests import HTTPError, RequestException

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util import aimedia
from cloudbot.util.ai_common import (
    Message,
    clear_history,
    get_or_create_history,
    truncate_or_paste,
    upload_history,
)
from cloudbot.util.web import get_session
from plugins.huggingface import FileIrcResponseWrapper
from plugins.ratelimit import Limit, check, record

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
GEMINI_TEXT_MODEL = "gemini-2.5-flash"

# Image model — Free tier ~15 RPM / ~500 RPD on flash-image (Mar 2026).
IMG_BUCKET = "gemini-img"
IMG_MAX_RPM = 8
IMG_MAX_RPH = 62
IMG_MAX_RPD = 450
IMG_LIMITS = [
    Limit(60, IMG_MAX_RPM, "Rate limited. Try again in a minute."),
    Limit(3600, IMG_MAX_RPH, "Hourly limit reached. Try again later."),
    Limit(86400, IMG_MAX_RPD, "Daily Gemini-image cap reached. Resets in 24h."),
]

# Video — slow and tightly capped.
VID_BUCKET = "aimedia-video"
VID_LIMITS = [
    Limit(60, 1, "One video at a time — wait a minute."),
    Limit(86400, 12, "Daily video cap reached. Resets in 24h."),
]

# Text model — Free tier 10 RPM / 250 RPD on gemini-2.5-flash.
TEXT_BUCKET = "gemini-text"
TEXT_MAX_RPM = 8
TEXT_MAX_RPD = 220
TEXT_LIMITS = [
    Limit(60, TEXT_MAX_RPM, "Rate limited. Try again in a minute."),
    Limit(86400, TEXT_MAX_RPD, "Daily Gemini-text cap reached. Resets in 24h."),
]

MAX_IMAGE_SIZE = 20 * 1024 * 1024
MAX_TEXT_HISTORY_LENGTH = 32

gemt_messages_cache: dict[tuple[str, str], Deque[Message]] = {}


def _get_api_key():
    api_key = bot.config.get_api_key("gemini")
    if not api_key:
        return (
            None,
            "Gemini API key not configured. Set 'gemini' in api_keys config.",
        )
    return api_key, None


def _upload_image(image_bytes, target):
    """Write the image to a temp file and upload it to the channel."""
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        f.write(image_bytes)
        f.flush()
        return FileIrcResponseWrapper.upload_file(f.name, target)


def _fetch_image(url):
    """Download URL and validate it's an image."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CloudBot/1.0)",
        "Accept": "image/*,*/*;q=0.8",
    }
    session = get_session()
    try:
        resp = session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except RequestException as e:
        return None, None, f"Failed to download image: {e}"

    content_type = resp.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        return None, None, f"URL is not an image (Content-Type: {content_type})"

    if len(resp.content) > MAX_IMAGE_SIZE:
        return None, None, "Image too large (max 20MB)."

    mime = content_type.split(";")[0].strip()
    return resp.content, mime, None


def _call_gemini_text(api_key, history):
    contents = [
        {
            "role": "user" if m.role == "user" else "model",
            "parts": [{"text": m.content}],
        }
        for m in history
    ]
    response = get_session().post(
        GEMINI_BASE + GEMINI_TEXT_MODEL + ":generateContent",
        params={"key": api_key},
        json={"contents": contents},
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        feedback = data.get("promptFeedback", {})
        if feedback.get("blockReason"):
            return None, f"Gemini blocked: {feedback['blockReason']}"
        return None, "Gemini returned no candidates."
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        return None, "Gemini returned no text."
    return text, None


@hook.command("gemimg")
def gemimg_command(text, chan, nick, db):
    """<prompt> - Generate an image with Gemini."""
    prompt = text.strip()
    if not prompt:
        return "Usage: .gemimg <prompt>"
    try:
        api_url, key = aimedia.config_from_bot(bot)
    except aimedia.MediaGenNotConfigured as e:
        return str(e)

    limit_msg = check(db, IMG_BUCKET, IMG_LIMITS)
    if limit_msg:
        return limit_msg

    try:
        images = aimedia.generate_image(api_url, key, prompt)
    except aimedia.MediaGenError as e:
        return f"media error: {e}"
    if not images:
        return "Gemini returned no image. Try a different prompt."
    record(db, IMG_BUCKET)
    return _upload_image(images[0], chan or nick)


@hook.command("gemedit")
def gemedit_command(text, chan, nick, db):
    """<url> <prompt> - Edit an image with Gemini."""
    parts_text = text.strip().split(None, 1)
    if len(parts_text) < 2:
        return "Usage: .gemedit <image_url> <prompt>"
    img_url, prompt = parts_text
    if not img_url.startswith(("http://", "https://")):
        return "First argument must be a URL."
    try:
        api_url, key = aimedia.config_from_bot(bot)
    except aimedia.MediaGenNotConfigured as e:
        return str(e)

    limit_msg = check(db, IMG_BUCKET, IMG_LIMITS)
    if limit_msg:
        return limit_msg

    image_data, _, err = _fetch_image(img_url)
    if err:
        return err

    try:
        images = aimedia.edit_image(
            api_url, key, prompt, base64.b64encode(image_data).decode()
        )
    except aimedia.MediaGenError as e:
        return f"media error: {e}"
    if not images:
        return "Gemini returned no image. Try a different prompt."
    record(db, IMG_BUCKET)
    return _upload_image(images[0], chan or nick)


# Disabled aimedia video path; .video is served by plugins/hyperframes.py.
# @hook.command("video")
# def video_command(text, chan, nick, conn, db):
#     """<prompt> - Generate a video from a text prompt."""
#     prompt = text.strip()
#     if not prompt:
#         return "Usage: .video <prompt>"
#     try:
#         api_url, key = aimedia.config_from_bot(bot)
#     except aimedia.MediaGenNotConfigured as e:
#         return str(e)
#
#     limit_msg = check(db, VID_BUCKET, VID_LIMITS)
#     if limit_msg:
#         return limit_msg
#
#     try:
#         job_id = aimedia.submit_video(api_url, key, prompt)
#     except aimedia.MediaGenError as e:
#         return f"media error: {e}"
#     record(db, VID_BUCKET)
#     aimedia.watch_video(
#         api_url, key, job_id, network=conn.name, chan=chan, nick=nick
#     )
#     return f"⏳ rendering video (job {job_id}) — I'll post the link here when it's ready (~3 min)."
#
#
# @hook.periodic(15, initial_interval=15)
# def video_watch_tick(bot):
#     """Post finished video links for any submit that has rendered."""
#
#     def post(network, chan, message):
#         conn = bot.connections.get(network)
#         if conn and conn.ready:
#             conn.message(chan, message)
#
#     aimedia.poll_watches(post)


@hook.command("gemt", "gai", "gae")
def gemt_command(text, nick, chan, db):
    """<text> - Chat with Google Gemini's free Flash text model."""
    api_key, err = _get_api_key()
    if err:
        return err

    prompt = text.strip()
    if not prompt:
        return "Usage: .gemt <text>"

    limit_msg = check(db, TEXT_BUCKET, TEXT_LIMITS)
    if limit_msg:
        return limit_msg

    history = get_or_create_history(
        gemt_messages_cache, chan, nick, MAX_TEXT_HISTORY_LENGTH
    )
    history.append(Message(role="user", content=prompt))
    try:
        response, err = _call_gemini_text(api_key, list(history))
    except HTTPError as e:
        history.pop()
        return f"Gemini API error: {e.response.status_code} {e.response.reason}"
    except RequestException as e:
        history.pop()
        return f"Request failed: {e}"

    if err is not None or response is None:
        history.pop()
        return err or "Gemini returned no text."

    record(db, TEXT_BUCKET)
    history.append(Message(role="assistant", content=response))
    return truncate_or_paste(
        response,
        nick,
        list(history),
        f"{nick}'s Gemini conversation in {chan}",
    )


@hook.command("gemtclear", autohelp=False)
def gemtclear_command(nick, chan):
    """Clear your Gemini text conversation."""
    return clear_history(gemt_messages_cache, chan, nick)


@hook.command("gemtpaste", "gemth", autohelp=False)
def gemtpaste_command(text, nick, chan):
    """[nick] - Paste your (or another nick's) Gemini text conversation."""
    target = text.strip() or nick
    channick = (chan, target)
    if channick not in gemt_messages_cache:
        return f"No Gemini conversation history for {target}."
    return upload_history(
        target,
        list(gemt_messages_cache[channick]),
        f"{target}'s Gemini conversation in {chan}",
    )
