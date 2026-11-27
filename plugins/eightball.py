import random
from typing import Annotated

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import colors

responses: list[str] = []


@hook.on_start()
def load_responses(bot: CloudBot) -> None:
    path = bot.data_path / "8ball_responses.txt"
    responses.clear()
    with open(path, encoding="utf-8") as f:
        responses.extend(
            line.strip() for line in f.readlines() if not line.startswith("//")
        )


@hook.command("8ball", "8", "eightball")
async def eightball(
    event: CommandEvent,
    question: Annotated[str, Arg("What to ask the magic 8 ball")],
):
    """asks the all knowing magic electronic eight ball a question"""
    magic = random.choice(responses)
    message = colors.parse(f"shakes the magic 8 ball... {magic}")

    event.action(message)
