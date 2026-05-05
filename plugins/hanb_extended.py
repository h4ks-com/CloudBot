from random import choice

from cloudbot import hook
from plugins.hanb import HANBS
from plugins.hanb_new import NEW_HANBS

ALL_HANBS = HANBS + NEW_HANBS


@hook.command("hanb", autohelp=False)
def hanb(text: str):
    """- Prints a random hanb"""
    ranb = choice(ALL_HANBS)
    return ranb.split("\n")
