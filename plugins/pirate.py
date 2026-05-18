from urllib.parse import quote_plus

from cloudbot import hook
from cloudbot.util.web import get_session

PIRATE_INSULT_API = "https://pirate.monkeyness.com/api/insult"
PIRATE_TRANSLATE_API = "https://pirate.monkeyness.com/api/translate?english="


@hook.command("pirateinsult", autohelp=False)
def pirate_insult() -> str:
    """- Get a random pirate insult."""
    try:
        response = get_session().get(PIRATE_INSULT_API, timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except Exception as e:
        return f"Error: {e}"


@hook.command("pirate")
def pirate_translate(text: str) -> str:
    """<text> - Translate text to pirate speak."""
    if not text.strip():
        return "Error: You must provide text to translate."
    try:
        url = PIRATE_TRANSLATE_API + quote_plus(text)
        response = get_session().get(url, timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except Exception as e:
        return f"Error: {e}"
