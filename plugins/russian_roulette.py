import random
from functools import lru_cache

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.util.colors import get_color, get_format

BULLETS = 6


@lru_cache(maxsize=1)
def get_barrel():
    index = random.randint(0, BULLETS - 1)
    return [False] * index + [True] + [False] * (BULLETS - index - 1)


@hook.command("russian_roulette", "rr", autohelp=False)
def rr(
    text: str,
    bot: CloudBot,
    chan: str,
    nick: str,
    event: CommandEvent,
):
    """- Start a game of Russian Roulette and test it."""
    barrel = get_barrel()
    shot = barrel.pop()
    if shot:
        event.reply(f"BANG! 🩸🤯 🔫   -  {get_color('red')}You died")
        get_barrel.cache_clear()
        return
    event.reply(
        f"{get_color('green')}You live{get_format('clear')}. \x02{len(barrel)}\x02 bullets left. Use \x1d.rrs\x1d to re-spin the barrel."
    )


@hook.command("rrspin", "rrs", autohelp=False)
def respun(
    text: str,
    bot: CloudBot,
    chan: str,
    nick: str,
    event: CommandEvent,
):
    """- Respins the russian roulette gun's barrel."""
    get_barrel.cache_clear()
    event.reply("Barrel re-spinning...")
