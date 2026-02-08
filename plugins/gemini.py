import base64
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
MAX_IMAGE_SIZE = 20 * 1024 * 1024

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


def _get_api_key():
    api_key = bot.config.get_api_key("gemini")
    if not api_key:
        return None, "Gemini API key not configured. Set 'gemini' in api_keys config."
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


def _fetch_image(url):
    """Download URL and validate it's an image."""
    session = get_session()
    try:
        resp = session.get(url, timeout=30)
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
