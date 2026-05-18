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

import base64
from typing import Any

import requests
from bs4 import BeautifulSoup

from cloudbot.bot import CloudBot

# Cloudflare cdnjs (and other CF sites) slow-walk script subresource requests
# from `HeadlessChrome` UA — fetch() works, but <script src> stalls until timeout.
# Override to a normal Chrome UA on every call.
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def _default_headers() -> dict[str, str]:
    return {"User-Agent": _DEFAULT_UA}


def get_config(bot: CloudBot) -> dict[str, Any]:
    cfg: dict[str, Any] = bot.config.get("browserless", {})
    return cfg


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
        "setExtraHTTPHeaders": _default_headers(),
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
        "setExtraHTTPHeaders": _default_headers(),
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
    data: dict[str, Any] = response.json()
    return data


def get_soup(
    url: str,
    bot: CloudBot,
    stealth: bool = False,
    timeout: int = 60,
) -> BeautifulSoup:
    """Fetch a page via Browserless and return a BeautifulSoup object."""
    response = fetch_content(url, bot, stealth=stealth, timeout=timeout)
    return BeautifulSoup(response.content, "html.parser")


_SCREENSHOT_FN = """
module.exports = async ({ page, context }) => {
  if (context.user_agent) await page.setUserAgent(context.user_agent);
  await page.setViewport({ width: context.width || 1280, height: context.height || 800 });
  try {
    await page.goto(context.url, {
      waitUntil: context.wait_until || 'networkidle0',
      timeout: context.nav_timeout_ms || 15000,
    });
  } catch (e) {
    // Navigation didn't reach the wait condition (animation loops, long-tail
    // connections, etc) — that's fine; capture whatever has painted so far.
  }
  if (context.settle_ms) {
    await new Promise(r => setTimeout(r, context.settle_ms));
  }
  const buf = await page.screenshot({ type: 'png', fullPage: !!context.full_page });
  return { data: buf.toString('base64'), type: 'application/json' };
};
"""


def take_screenshot(
    url: str,
    bot: CloudBot,
    timeout: int = 15,
    extra_wait_ms: int = 2000,
    full_page: bool = False,
) -> bytes:
    """Render a page in a real browser and return a PNG screenshot as bytes.

    Uses `networkidle0` like the classic Puppeteer screenshot recipe, but if
    the goto times out (rAF-driven canvases, persistent connections, etc) we
    still take the screenshot of whatever was painted — never raise.

    `timeout` is the navigation budget in seconds; `extra_wait_ms` is a final
    settle pause before capturing.
    """
    result = run_function(
        _SCREENSHOT_FN,
        bot,
        context={
            "url": url,
            "user_agent": _DEFAULT_UA,
            "nav_timeout_ms": timeout * 1000,
            "settle_ms": extra_wait_ms,
            "full_page": full_page,
        },
        timeout=timeout + extra_wait_ms // 1000 + 10,
    )
    if isinstance(result, dict):
        b64 = result.get("data") or ""
    else:
        b64 = result if isinstance(result, str) else ""
    if not b64:
        raise ValueError("browserless returned no screenshot data")
    return base64.b64decode(b64)


def run_function(
    code: str,
    bot: CloudBot,
    context: dict[str, Any] | None = None,
    timeout: int = 60,
) -> Any:
    """POST to /function — run arbitrary Puppeteer code and return its result.

    `code` must be a CommonJS module exporting an async function:
        module.exports = async ({ page, context }) => { ... return value; }

    `context` is passed as the second arg to that function.
    Returns the JSON-decoded body when browserless responds with application/json,
    otherwise the raw response text.
    """
    cfg = get_config(bot)
    api_url = cfg["api_url"].rstrip("/")
    token = cfg["api_token"]

    payload: dict[str, Any] = {"code": code, "context": context or {}}

    response = requests.post(
        f"{api_url}/function",
        params={"token": token},
        json=payload,
        timeout=timeout + 5,
    )
    response.raise_for_status()
    ct = response.headers.get("content-type", "")
    if "application/json" in ct:
        return response.json()
    return response.text


_CONSOLE_FN = """
module.exports = async ({ page, context }) => {
  if (context.user_agent) await page.setUserAgent(context.user_agent);
  const messages = [];
  page.on('console', msg => {
    messages.push({ type: msg.type(), text: msg.text() });
  });
  page.on('pageerror', err => {
    messages.push({ type: 'pageerror', text: err.message, stack: err.stack || '' });
  });
  page.on('requestfailed', req => {
    messages.push({ type: 'requestfailed', text: req.url() + ' - ' + (req.failure() ? req.failure().errorText : 'unknown') });
  });
  try {
    await page.goto(context.url, {
      waitUntil: context.wait_until || 'networkidle0',
      timeout: context.nav_timeout_ms || 15000,
    });
  } catch (e) {
    // Nav didn't reach idle — capture whatever errors fired during partial load.
    messages.push({ type: 'navigationerror', text: e.message });
  }
  if (context.settle_ms) {
    await new Promise(r => setTimeout(r, context.settle_ms));
  }
  return { data: messages, type: 'application/json' };
};
"""


def fetch_console_logs(
    url: str,
    bot: CloudBot,
    settle_ms: int = 2000,
    timeout: int = 15,
    wait_until: str = "networkidle0",
) -> list[dict[str, Any]]:
    """Load `url` in a browser and capture console messages, page errors, failed requests.

    On goto timeout, captured messages are still returned (a 'navigationerror'
    entry is appended). Never raises on slow/never-idle pages.
    """
    result = run_function(
        _CONSOLE_FN,
        bot,
        context={
            "url": url,
            "settle_ms": settle_ms,
            "nav_timeout_ms": timeout * 1000,
            "wait_until": wait_until,
            "user_agent": _DEFAULT_UA,
        },
        timeout=timeout + settle_ms // 1000 + 10,
    )
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "data" in result:
        messages: list[dict[str, Any]] = result["data"]
        return messages
    return []


_EVAL_FN = """
module.exports = async ({ page, context }) => {
  if (context.user_agent) await page.setUserAgent(context.user_agent);
  let nav_err = null;
  try {
    await page.goto(context.url, {
      waitUntil: context.wait_until || 'networkidle0',
      timeout: context.nav_timeout_ms || 15000,
    });
  } catch (e) {
    nav_err = e.message;
  }
  if (context.settle_ms) {
    await new Promise(r => setTimeout(r, context.settle_ms));
  }
  const result = await page.evaluate((s) => {
    try {
      const r = (0, eval)(s);
      return { ok: true, value: (typeof r === 'object' && r !== null) ? JSON.parse(JSON.stringify(r)) : r };
    } catch (e) {
      return { ok: false, error: e.message, stack: e.stack || '' };
    }
  }, context.script);
  if (nav_err) result.nav_warning = nav_err;
  return { data: result, type: 'application/json' };
};
"""


def evaluate_in_page(
    url: str,
    script: str,
    bot: CloudBot,
    settle_ms: int = 0,
    timeout: int = 15,
) -> dict[str, Any]:
    """Load `url` and evaluate `script` (JS expression or block) in page context.

    Returns a dict with either {"ok": True, "value": ...} or {"ok": False, "error": ...}.
    On nav timeout the script is still evaluated; result includes `nav_warning`.
    """
    result = run_function(
        _EVAL_FN,
        bot,
        context={
            "url": url,
            "script": script,
            "settle_ms": settle_ms,
            "nav_timeout_ms": timeout * 1000,
            "user_agent": _DEFAULT_UA,
        },
        timeout=timeout + settle_ms // 1000 + 10,
    )
    if isinstance(result, dict) and "data" in result:
        payload_data: dict[str, Any] = result["data"]
        return payload_data
    if isinstance(result, dict):
        return result
    return {"ok": False, "error": "unexpected response shape", "value": result}
