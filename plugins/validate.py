import urllib.parse
from typing import Annotated

import requests

from cloudbot import hook
from cloudbot.hook import Arg
from cloudbot.util import web
from cloudbot.util.web import get_session

api_url = "https://validator.w3.org/nu/"


@hook.command("validate", "w3c")
def validate(url: Annotated[str, Arg("Page to validate")]):
    """Runs url through the W3C Markup Validator."""
    if not urllib.parse.urlparse(url).scheme:
        url = "http://" + url

    params = {"doc": url, "out": "json"}
    headers = {
        "User-Agent": "CloudBot/IRC (https://github.com/TotallyNotRobots/CloudBot)"
    }

    try:
        request = get_session().get(
            api_url, params=params, headers=headers, timeout=10
        )
        request.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            return "The W3C validator is rate limiting requests. Please try again later."
        return f"Failed to validate: HTTP {e.response.status_code}"
    except requests.exceptions.RequestException as e:
        return f"Failed to connect to validator: {type(e).__name__}"

    response = request.json()
    messages = response.get("messages", [])

    error_count = sum(1 for m in messages if m.get("type") == "error")
    warning_count = sum(
        1
        for m in messages
        if m.get("type") == "info" and m.get("subType") == "warning"
    )

    out_warning = "warnings" if warning_count != 1 else "warning"
    out_error = "errors" if error_count != 1 else "error"

    validator_url = f"{api_url}?doc={urllib.parse.quote(url)}"
    short_url = web.try_shorten(validator_url)

    return f"{url} has {warning_count} {out_warning} and {error_count} {out_error} ({short_url})"
