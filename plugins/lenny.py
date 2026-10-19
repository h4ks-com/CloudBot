import json
import random

from cloudbot import hook
from cloudbot.event import CommandEvent

lenny_data: dict[str, list[str]] = {}


@hook.on_start()
def load_faces(bot):
    lenny_data.clear()
    data_file = bot.data_path / "lenny.json"
    with data_file.open(encoding="utf-8") as f:
        lenny_data.update(json.load(f))


@hook.command(autohelp=False)
def lenny(event: CommandEvent):
    """- why the shit not lennyface"""
    event.message(random.choice(lenny_data["lenny"]))


@hook.command(autohelp=False)
def flenny(event: CommandEvent):
    """- flenny is watching."""
    event.message(random.choice(lenny_data["flenny"]))
