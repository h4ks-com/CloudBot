import json
import random
import re
from typing import Annotated

from cloudbot import hook
from cloudbot.hook import Arg

gnomecards: dict[str, list[str]] = {}


@hook.on_start()
def shuffle_deck(bot):
    gnomecards.clear()
    with open((bot.data_path / "gnomecards.json"), encoding="utf-8") as f:
        gnomecards.update(json.load(f))


@hook.command("cah")
def CAHwhitecard(text: Annotated[str, Arg("Text for the whitecard")]):
    """Submit text to be used as a CAH whitecard"""
    return random.choice(gnomecards["black"]).format(text)


@hook.command("cahb")
def CAHblackcard(text: Annotated[str, Arg("Text with _ to fill in")]):
    """Submit text with _ for the bot to fill in the rest. You can submit text with multiple _"""
    CardText = text.strip()

    # noinspection PyUnusedLocal
    def blankfiller(matchobj):
        return random.choice(gnomecards["white"])

    out = re.sub(r"\b_\b", blankfiller, CardText)
    return out
