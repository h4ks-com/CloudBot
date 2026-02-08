import base64
import logging
import tempfile

from requests import HTTPError, RequestException

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session
from plugins.huggingface import FileIrcResponseWrapper

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"


@hook.command("gemimg")
def gemimg_command(text, chan, nick):
    """<prompt> - Generate an image using Google Gemini. Get API key at https://aistudio.google.com/apikey"""
    api_key = bot.config.get_api_key("gemini")
    if not api_key:
        return "Gemini API key not configured. Set 'gemini' in api_keys config."

    prompt = text.strip()
    if not prompt:
        return "Usage: .gemimg <prompt>"

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
    logger = logging.getLogger("cloudbot")
    logger.info("[gemini] response: %s", {k: v for k, v in data.items() if k != "candidates"})
    candidates = data.get("candidates", [{}])
    if candidates:
        logger.info("[gemini] candidate keys: %s", list(candidates[0].keys()))
        content = candidates[0].get("content", {})
        logger.info("[gemini] parts: %s", [{k: v for k, v in p.items() if k != "data"} for p in content.get("parts", [])])
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
