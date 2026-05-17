"""
issafe.py

Check a URL against the Google Safe Browsing API (v4).

License:
    GNU General Public License (Version 3)
"""

from urllib.parse import urlparse

from requests import HTTPError, RequestException

import cloudbot
from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session

API_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
THREAT_TYPES = [
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
]


@hook.command()
def issafe(text):
    """<website> - Checks the website against Google's Safe Browsing list."""
    if urlparse(text).scheme not in ("https", "http"):
        return "Check your URL (it should be a complete URI)."

    api_key = bot.config.get_api_key("google")
    if not api_key:
        return "This command requires a Google API key."

    payload = {
        "client": {
            "clientId": "cloudbot",
            "clientVersion": str(cloudbot.__version__),
        },
        "threatInfo": {
            "threatTypes": THREAT_TYPES,
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": text}],
        },
    }

    try:
        response = get_session().post(
            API_URL, params={"key": api_key}, json=payload, timeout=10
        )
        response.raise_for_status()
    except HTTPError as e:
        return f"Safe Browsing API error: {e.response.status_code} {e.response.reason}"
    except RequestException as e:
        return f"Safe Browsing request failed: {e}"

    matches = response.json().get("matches") or []
    if not matches:
        return f"\x02{text}\x02 is safe."

    threats = ", ".join(
        sorted({m.get("threatType", "UNKNOWN") for m in matches})
    )
    return f"\x02{text}\x02 is flagged: {threats}"
