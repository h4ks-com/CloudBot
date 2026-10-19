import random
from typing import Annotated

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg

fmklist: list[str] = []


@hook.on_start()
def load_fmk(bot: CloudBot) -> None:
    fmklist.clear()
    with open(bot.data_path / "fmk.txt", encoding="utf-8") as f:
        fmklist.extend(
            line.strip() for line in f.readlines() if not line.startswith("//")
        )


@hook.command("fmk", autohelp=False)
def fmk(
    event: CommandEvent,
    target: Annotated[
        str | None, Arg("Who to play with", kind="user", rest=True)
    ] = None,
):
    """Fuck, Marry, Kill"""
    event.message(
        " {} FMK - {}, {}, {}".format(
            (target or "").strip(),
            random.choice(fmklist),
            random.choice(fmklist),
            random.choice(fmklist),
        )
    )
