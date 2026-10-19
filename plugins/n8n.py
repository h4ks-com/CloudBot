# H4ks specific n8n webhooks
from typing import Annotated

import requests

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting
from cloudbot.util.web import get_session

base_url = "https://n8n.h4ks.com/webhook"


def request_webhook(
    endpoint: str, json_data: dict | None = None
) -> requests.Response:
    return get_session().post(f"{base_url}/{endpoint}", json=json_data)


@hook.command("meme")
def meme(
    nick: str,
    prompt: Annotated[str, Arg("Prompt for the meme")],
    event: CommandEvent,
) -> None:
    """Generate a meme with the given prompt."""
    response = request_webhook("memegenerator", json_data={"prompt": prompt})
    try:
        response.raise_for_status()
        data = response.json()
        event.reply(data["url"])
    except requests.HTTPError:
        event.reply(
            f"Error reaching n8n: {response.status_code} - {formatting.truncate(response.text, 200)}"
        )
