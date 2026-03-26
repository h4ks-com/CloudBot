import os

import requests

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.util import web
from cloudbot.util.browserless import is_configured, take_screenshot
from cloudbot.util.web import get_session

FILEBIN_URL = os.environ.get("FILEBIN_URL", "https://s.h4ks.com")


def _upload_image(image_bytes: bytes) -> str:
    files = {"file": ("screenshot.png", image_bytes, "image/png")}
    response = get_session().post(FILEBIN_URL, files=files, timeout=30)
    response.raise_for_status()
    return response.text.strip()


@hook.command("screenshot", "ss")
def screenshot(text: str, bot: CloudBot) -> str:
    """<url> - Take a screenshot of a webpage and upload it"""
    url = text.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not is_configured(bot):
        return "Screenshot: browserless not configured"

    try:
        image_bytes = take_screenshot(url, bot)
    except requests.HTTPError as e:
        return f"Screenshot failed: {e.response.status_code}"
    except requests.RequestException:
        return "Screenshot failed: connection error"

    try:
        image_url = _upload_image(image_bytes)
    except requests.HTTPError as e:
        return f"Upload failed: {e.response.status_code}"
    except requests.RequestException:
        return "Upload failed: connection error"

    return web.try_shorten(image_url)
