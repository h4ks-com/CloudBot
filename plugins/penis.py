import random
from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg

balls = ["(_)_)", "8", "B", "(___)__)", "(_)(_)", "(@)@)", "3"]
shaft = [
    "=",
    "==",
    "===",
    "====",
    "=====",
    "========",
    "/////////////////////////",
    "|||||||||||||",
    "\u2248\u2248\u2248",
]
head = ["D", "Q", ">", "|\u2283", "\u22d1", "\u22d9", "\u22d7"]
emission = ["~ ~ ~ ~", "~ * ~ &", "", "*~* *~* %"]
bodypart = [
    "face",
    "glasses",
    "thigh",
    "tummy",
    "back",
    "hiney",
    "hair",
    "boobs",
    "tongue",
]


@hook.command("penis", "bepis", autohelp=False)
def penis(
    event: CommandEvent,
    nick: Annotated[
        str | None, Arg("Who to target", kind="user", rest=True)
    ] = None,
):
    """much dongs, very ween, add a user nick as an arguement for slightly different 'output'"""
    if not nick:
        event.message(
            f"{random.choice(balls)}{random.choice(shaft)}{random.choice(head)}"
        )
    else:
        person = nick.split(" ")[0]
        event.message(
            f"{random.choice(balls)}{random.choice(shaft)}{random.choice(head)}{random.choice(emission)} all over {person}'s {random.choice(bodypart)}"
        )
