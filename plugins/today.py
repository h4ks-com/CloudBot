import random

from cloudbot import hook
from cloudbot.util import http


@hook.command(autohelp=False)
def today(reply) -> str:
    """- Returns a random historical fact that happened on this day"""
    try:
        response = http.get_json("https://history.muffinlabs.com/date")

        all_events = response["data"]["Events"]
        if not all_events:
            return "No historical events found for today."

        random_event = random.choice(all_events)
        year = random_event["year"]
        text = random_event["text"]

        return f"On this day in {year}: {text}"

    except Exception:
        reply("There was an error getting today's historical facts.")
        raise
