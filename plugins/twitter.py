from cloudbot import hook
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent


@hook.command("twitter", "tw", "twatter")
def twitter(text: str, event: CommandEvent, conn: IrcClient):
    """- The Twitter API is no longer available."""

    return "The Twitter API is no longer available."


@hook.command("twuser", "twinfo")
def twuser(text: str, event: CommandEvent):
    """- The Twitter API is no longer available."""

    return "The Twitter API is no longer available."
