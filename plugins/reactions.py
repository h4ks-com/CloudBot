import json
import random
from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg

deal_with_it_phrases = [
    "Stop complaining, \x02{}\x02, and",
    "Jesus fuck \x02{}\x02, just",
    "Looks like \x02{}\x02 needs to",
    "Ever think that \x02{}\x02 just needs to",
]
reaction_macros: dict[str, list[str]] = {}


@hook.on_start()
def load_macros(bot):
    reaction_macros.clear()
    with open(
        (bot.data_path / "reaction_macros.json"), encoding="utf-8"
    ) as macros:
        reaction_macros.update(json.load(macros))


@hook.command("dwi", "dealwithit")
def deal_with_it(
    event: CommandEvent,
    target: Annotated[str, Arg("Who needs to deal with it", kind="user")],
):
    """Tell the nick in the channel to deal with it. Code located in reactions.py"""
    person_needs_to_deal = target.strip()
    phrase = random.choice(deal_with_it_phrases)
    formated_phrase = phrase.format(person_needs_to_deal)
    event.message(
        "{} {}".format(
            formated_phrase,
            random.choice(reaction_macros["deal_with_it_macros"]),
        )
    )


@hook.command("fp", "facepalm")
def face_palm(
    event: CommandEvent,
    target: Annotated[str, Arg("Who frustrates you", kind="user")],
):
    """Expresses your frustration with the nick. Code located in reactions.py"""
    face_palmer = target.strip()
    event.message(
        "Dammit {} {}".format(
            face_palmer, random.choice(reaction_macros["facepalm_macros"])
        )
    )


@hook.command("hd", "headdesk")
def head_desk(
    event: CommandEvent,
    target: Annotated[str, Arg("Who drives you to it", kind="user")],
):
    """Hit your head against the desk becausae of the nick. Code located in reactions.py"""
    idiot = target.strip()
    event.message(
        "{} {}".format(
            idiot, random.choice(reaction_macros["head_desk_macros"])
        )
    )


@hook.command("fetish", "tmf")
def my_fetish(
    event: CommandEvent,
    target: Annotated[str, Arg("Who to tell", kind="user")],
):
    """Did some one just mention what your fetish was? Let the nick know! Code located in reactions.py"""
    person_to_share_fetish_with = target.strip()
    event.message(
        "{} {}".format(
            person_to_share_fetish_with,
            random.choice(reaction_macros["fetish_macros"]),
        )
    )
