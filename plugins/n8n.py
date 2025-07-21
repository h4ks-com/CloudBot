# H4ks specific n8n webhooks
import requests

from cloudbot import hook
from cloudbot.util import formatting

base_url = "https://n8n.h4ks.com/webhook"


def request_webhook(endpoint: str, json_data: dict | None = None) -> requests.Response:
    return requests.post(f"{base_url}/{endpoint}", json=json_data)


@hook.command("meme")
def meme(nick: str, text: str, reply) -> None:
    """<prompt> - Generate a meme with the given prompt."""
    if not text.strip():
        reply("Usage: .meme <prompt>")
        return
    response = request_webhook("memegenerator", json_data={"prompt": text})
    try:
        response.raise_for_status()
        data = response.json()
        reply(data["url"])
    except requests.HTTPError:
        reply(f"Error reaching n8n: {response.status_code} - {formatting.truncate(response.text, 200)}")
