# Funny prank

from random import choice
from typing import Annotated

from cloudbot import hook
from cloudbot.hook import Arg

SENTENCES = [
    "Leaves a dump on {}'s head",
    "Poops hard over {}'s foot",
    "Sharts on {}'s face",
    "Defecates out of control all around {}",
    "Farts and poops while {} smells it",
    "Feeds {} by pooping on his mouth,",
]


@hook.command("poop", autohelp=False)
def poop(target: Annotated[str | None, Arg("Who to poop on")] = None):
    """Makes the bot poop on [target]"""
    if not target:
        return "💩"
    return choice(SENTENCES).format(target)
