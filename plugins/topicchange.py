import random

from cloudbot import hook
from cloudbot.event import CommandEvent

topicchange_data: list[str] = []


@hook.on_start()
def load_topicchange(bot):
    topicchange_data.clear()
    with open((bot.data_path / "topicchange.txt"), encoding="utf-8") as f:
        topicchange_data.extend(
            line.strip() for line in f.readlines() if not line.startswith("//")
        )


@hook.command("changetopic", "discuss", "question", autohelp=False)
def topicchange(event: CommandEvent):
    """- generates a random question to help start a conversation or change a topic"""
    event.message(random.choice(topicchange_data))
