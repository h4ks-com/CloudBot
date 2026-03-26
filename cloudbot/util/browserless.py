"""Browserless v1 cloud browser integration.

Provides helpers to fetch rendered HTML via the Browserless /content endpoint,
with optional stealth mode to bypass bot detection.

Config (in config.json under "browserless"):
    {
        "browserless": {
            "api_url": "https://chrome.browserless.io",
            "api_token": "your-token"
        }
    }

Available endpoints on this v1 instance:
    /content   — render page and return HTML (supports stealth flag)
    /scrape    — structured extraction via CSS selectors
    /function  — arbitrary Puppeteer JS code
    /screenshot — take a screenshot
"""

from typing import Any

import requests
from bs4 import BeautifulSoup

from cloudbot.bot import CloudBot


def get_config(bot: CloudBot) -> dict[str, Any]:
    return bot.config.get("browserless", {})


def is_configured(bot: CloudBot) -> bool:
    cfg = get_config(bot)
    return bool(cfg.get("api_url") and cfg.get("api_token"))


def fetch_content(
    url: str,
    bot: CloudBot,
    stealth: bool = False,
    timeout: int = 60,
    wait_until: str = "networkidle2",
) -> requests.Response:
    """POST to /content — renders the page in a real browser and returns HTML.

    Args:
        stealth: Enable stealth mode to bypass basic bot detection.
        wait_until: Puppeteer waitUntil option. "networkidle2" is usually enough;
                    use "networkidle0" for heavier SPAs.

    Raises requests.HTTPError on non-2xx responses.
    """
    cfg = get_config(bot)
    api_url = cfg["api_url"].rstrip("/")
    token = cfg["api_token"]

    payload: dict[str, Any] = {
        "url": url,
        "gotoOptions": {"waitUntil": wait_until, "timeout": timeout * 1000},
    }
    if stealth:
        payload["stealth"] = True

    response = requests.post(
        f"{api_url}/content",
        params={"token": token},
        json=payload,
        timeout=timeout + 5,
    )
    response.raise_for_status()
    return response


def fetch_scrape(
    url: str,
    selectors: list[str],
    bot: CloudBot,
    stealth: bool = False,
    timeout: int = 60,
) -> dict[str, Any]:
    """POST to /scrape — structured extraction via CSS selectors.

    Returns the parsed JSON response from Browserless.
    Each selector maps to a list of extracted {text, attributes} objects.

    Raises requests.HTTPError on non-2xx responses.
    """
    cfg = get_config(bot)
    api_url = cfg["api_url"].rstrip("/")
    token = cfg["api_token"]

    payload: dict[str, Any] = {
        "url": url,
        "elements": [{"selector": s} for s in selectors],
        "gotoOptions": {"waitUntil": "networkidle2", "timeout": timeout * 1000},
    }
    if stealth:
        payload["stealth"] = True

    response = requests.post(
        f"{api_url}/scrape",
        params={"token": token},
        json=payload,
        timeout=timeout + 5,
    )
    response.raise_for_status()
    return response.json()


def get_soup(
    url: str,
    bot: CloudBot,
    stealth: bool = False,
    timeout: int = 60,
) -> BeautifulSoup:
    """Fetch a page via Browserless and return a BeautifulSoup object."""
    response = fetch_content(url, bot, stealth=stealth, timeout=timeout)
    return BeautifulSoup(response.content, "html.parser")


def take_screenshot(
    url: str,
    bot: CloudBot,
    timeout: int = 30,
    extra_wait_ms: int = 4000,
) -> bytes:
    """Render a page in a real browser and return a PNG screenshot as bytes.

    extra_wait_ms is added after networkidle2 to let late-loading content settle.
    """
    cfg = get_config(bot)
    api_url = cfg["api_url"].rstrip("/")
    token = cfg["api_token"]

    payload: dict[str, Any] = {
        "url": url,
        "gotoOptions": {"waitUntil": "networkidle2", "timeout": timeout * 1000},
        "waitFor": extra_wait_ms,
    }

    response = requests.post(
        f"{api_url}/screenshot",
        params={"token": token},
        json=payload,
        timeout=timeout + extra_wait_ms // 1000 + 5,
    )
    response.raise_for_status()
    return response.content
