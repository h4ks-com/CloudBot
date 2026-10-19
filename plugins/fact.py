import random

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.util import http

types = ["trivia", "math", "date", "year"]


@hook.command(autohelp=False)
def fact(event: CommandEvent):
    """- Gets a random fact about numbers or dates."""
    fact_type = random.choice(types)
    try:
        json = http.get_json(f"http://numbersapi.com/random/{fact_type}?json")
    except Exception:
        event.reply("There was an error contacting the numbersapi.com API.")
        raise

    response = json["text"]
    return response
