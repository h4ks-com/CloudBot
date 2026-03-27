import requests

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.util import web
from cloudbot.util.browserless import is_configured, take_screenshot


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

    image_url = web.paste(image_bytes, ext="png")
    return web.try_shorten(image_url)
