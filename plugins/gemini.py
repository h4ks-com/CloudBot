import base64
import logging
import tempfile
import time
from collections import deque

from requests import HTTPError, RequestException

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session
from plugins.huggingface import FileIrcResponseWrapper

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
MAX_RPM = 8
MAX_RPH = 62
MAX_RPD = 450
MAX_IMAGE_SIZE = 20 * 1024 * 1024

_request_times = deque()


def _check_ratelimit():
    now = time.monotonic()
    while _request_times and now - _request_times[0] > 86400:
        _request_times.popleft()

    recent_min = sum(1 for t in _request_times if now - t <= 60)
    if recent_min >= MAX_RPM:
        return "Rate limited. Try again in a minute."

    recent_hour = sum(1 for t in _request_times if now - t <= 3600)
    if recent_hour >= MAX_RPH:
        return "Hourly limit reached. Try again later."

    if len(_request_times) >= MAX_RPD:
        return "Daily free-tier limit reached. Resets in 24h."

    return None


def _record_call():
    _request_times.append(time.monotonic())


def _get_api_key():
    api_key = bot.config.get_api_key("gemini")
    if not api_key:
        return (
            None,
            "Gemini API key not configured. Set 'gemini' in api_keys config.",
        )
    return api_key, None


def _call_gemini(api_key, parts, chan, nick):
    session = get_session()
    try:
        response = session.post(
            API_URL,
            params={"key": api_key},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            },
        )
        response.raise_for_status()
    except HTTPError as e:
        return f"Gemini API error: {e.response.status_code} {e.response.reason}"
    except RequestException as e:
        return f"Request failed: {e}"

    _record_call()
    data = response.json()
    logger = logging.getLogger("cloudbot")
    candidates = data.get("candidates", [])

    if not candidates:
        logger.warning("[gemini] No candidates in response: %s", data)
        if "promptFeedback" in data:
            feedback = data["promptFeedback"]
            if feedback.get("blockReason"):
                return f"Gemini blocked: {feedback['blockReason']}"
        return "Gemini returned no results. Check logs for details."

    candidate = candidates[0]
    logger.info(
        "[gemini] Candidate: %s",
        {k: v for k, v in candidate.items() if k != "content"},
    )

    if "finishReason" in candidate:
        reason = candidate["finishReason"]
        if reason != "STOP":
            logger.warning("[gemini] Unusual finish reason: %s", reason)
            if "finishMessage" in candidate:
                msg = (
                    candidate["finishMessage"]
                    .replace("[send feedback]", "")
                    .replace(
                        "(https://ai.google.dev/gemini-api/docs/troubleshooting)",
                        "",
                    )
                    .strip()
                )
                return msg
            if reason in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
                return f"Gemini refused to generate: {reason}"

    parts = candidate.get("content", {}).get("parts", [])
    text_parts = []

    for part in parts:
        if "inlineData" in part:
            image_bytes = base64.b64decode(part["inlineData"]["data"])
            mime = part["inlineData"].get("mimeType", "image/png")
            ext = mime.split("/")[-1]
            with tempfile.NamedTemporaryFile(suffix=f".{ext}") as f:
                f.write(image_bytes)
                f.flush()
                return FileIrcResponseWrapper.upload_file(f.name, chan or nick)
        if "text" in part:
            text_parts.append(part["text"])

    logger.warning(
        "[gemini] No image in response. Parts: %s",
        [list(p.keys()) for p in parts],
    )
    if text_parts:
        text = " ".join(text_parts)[:150]
        return f"Gemini returned text only: {text}..."

    return "Gemini returned no image. Try a different prompt."


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


@hook.command("gemimg")
def gemimg_command(text, chan, nick):
    """<prompt> - Generate an image using Google Gemini."""
    api_key, err = _get_api_key()
    if err:
        return err

    prompt = text.strip()
    if not prompt:
        return "Usage: .gemimg <prompt>"

    limit_msg = _check_ratelimit()
    if limit_msg:
        return limit_msg

    return _call_gemini(api_key, [{"text": prompt}], chan, nick)


@hook.command("gemedit")
def gemedit_command(text, chan, nick):
    """<url> <prompt> - Edit an image using Google Gemini."""
    api_key, err = _get_api_key()
    if err:
        return err

    parts_text = text.strip().split(None, 1)
    if len(parts_text) < 2:
        return "Usage: .gemedit <image_url> <prompt>"

    url, prompt = parts_text

    if not url.startswith(("http://", "https://")):
        return "First argument must be a URL."

    limit_msg = _check_ratelimit()
    if limit_msg:
        return limit_msg

    image_data, mime, err = _fetch_image(url)
    if err:
        return err

    b64 = base64.b64encode(image_data).decode()
    return _call_gemini(
        api_key,
        [
            {"text": prompt},
            {"inlineData": {"mimeType": mime, "data": b64}},
        ],
        chan,
        nick,
    )
