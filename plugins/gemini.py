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

_request_times = deque()


def _check_ratelimit():
    now = time.monotonic()
    while _request_times and now - _request_times[0] > 3600:
        _request_times.popleft()

    recent = sum(1 for t in _request_times if now - t <= 60)
    if recent >= MAX_RPM:
        return "Rate limited. Try again in a minute."
    if len(_request_times) >= MAX_RPH:
        return "Hourly limit reached. Try again later."

    _request_times.append(now)
    return None


@hook.command("gemimg")
def gemimg_command(text, chan, nick):
    """<prompt> - Generate an image using Google Gemini. Get API key at https://aistudio.google.com/apikey"""
    api_key = bot.config.get_api_key("gemini")
    if not api_key:
        return "Gemini API key not configured. Set 'gemini' in api_keys config."

    prompt = text.strip()
    if not prompt:
        return "Usage: .gemimg <prompt>"

    limit_msg = _check_ratelimit()
    if limit_msg:
        return limit_msg

    session = get_session()
    try:
        response = session.post(
            API_URL,
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            },
        )
        response.raise_for_status()
    except HTTPError as e:
        return f"Gemini API error: {e.response.status_code} {e.response.reason}"
    except RequestException as e:
        return f"Request failed: {e}"

    data = response.json()
    candidates = data.get("candidates", [{}])
    for part in candidates[0].get("content", {}).get("parts", []):
        if "inlineData" in part:
            image_bytes = base64.b64decode(part["inlineData"]["data"])
            mime = part["inlineData"].get("mimeType", "image/png")
            ext = mime.split("/")[-1]
            with tempfile.NamedTemporaryFile(suffix=f".{ext}") as f:
                f.write(image_bytes)
                f.flush()
                return FileIrcResponseWrapper.upload_file(f.name, chan or nick)

    return "No image in Gemini response."
