import urllib.parse

import requests

from cloudbot import hook
from cloudbot.util import web
from cloudbot.util.web import get_session

api_url = "https://validator.w3.org/nu/"


@hook.command("validate", "w3c")
def validate(text):
    """<url> - Runs url through the W3C Markup Validator."""
    text = text.strip()

    if not urllib.parse.urlparse(text).scheme:
        text = "http://" + text

    params = {"doc": text, "out": "json"}
    headers = {
        "User-Agent": "CloudBot/IRC (https://github.com/TotallyNotRobots/CloudBot)"
    }

    try:
        request = get_session().get(api_url, params=params, headers=headers, timeout=10)
        request.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            return (
                "The W3C validator is rate limiting requests. Please try again later."
            )
        return f"Failed to validate: HTTP {e.response.status_code}"
    except requests.exceptions.RequestException as e:
        return f"Failed to connect to validator: {type(e).__name__}"

    response = request.json()
    messages = response.get("messages", [])

    error_count = sum(1 for m in messages if m.get("type") == "error")
    warning_count = sum(
        1 for m in messages if m.get("type") == "info" and m.get("subType") == "warning"
    )

    out_warning = "warnings" if warning_count != 1 else "warning"
    out_error = "errors" if error_count != 1 else "error"

    validator_url = f"{api_url}?doc={urllib.parse.quote(text)}"
    short_url = web.try_shorten(validator_url)

    return f"{text} has {warning_count} {out_warning} and {error_count} {out_error} ({short_url})"
