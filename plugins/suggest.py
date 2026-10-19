import json
from typing import Annotated

import requests

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting
from cloudbot.util.web import get_session


@hook.command()
def suggest(
    event: CommandEvent, phrase: Annotated[str, Arg("Phrase to complete")]
):
    """Gets suggested phrases for a google search"""
    params = {"output": "json", "client": "hp", "q": phrase}

    try:
        request = get_session().get(
            "http://google.com/complete/search", params=params
        )
        request.raise_for_status()
    except (
        requests.exceptions.HTTPError,
        requests.exceptions.ConnectionError,
    ) as e:
        event.reply(f"Could not get suggestions: {e}")
        raise

    page = request.text

    page_json = page.split("(", 1)[1][:-1]

    suggestions = json.loads(page_json)[1]
    suggestions = [suggestion[0] for suggestion in suggestions]

    if not suggestions:
        return "No suggestions found."

    out = formatting.strip_html(", ".join(suggestions))

    return formatting.truncate(out, 200)
