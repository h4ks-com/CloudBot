from typing import Annotated

from cloudbot import hook
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg


def get_latest_line(text, conn, chan, nick):
    for name, _, msg in reversed(conn.history.get(chan.casefold(), [])):
        if nick.casefold() == name.casefold():
            # Ignore commands
            if msg.startswith(conn.config["command_prefix"]):
                continue
            if not text:
                return msg
            elif text in msg:
                return msg

    return None


@hook.command()
def mock(
    chan: str,
    conn: IrcClient,
    event: CommandEvent,
    target: Annotated[str, Arg("Whose last message to mock", kind="user")],
    needle: Annotated[str | None, Arg("Text the message must contain")] = None,
):
    """turn the user's last message in to aLtErNaTiNg cApS"""
    line = get_latest_line(needle or "", conn, chan, target)
    if line is None:
        return f"Nothing found in recent history for {target}"

    if line.startswith("\x01ACTION"):
        fmt = "* {nick} {msg}"
        line = line[8:].strip(" \x01")
    else:
        fmt = "<{nick}> {msg}"

    # Return the message in aLtErNaTiNg cApS
    line = "".join(
        c.upper() if i & 1 else c.lower() for i, c in enumerate(line)
    )
    event.message(fmt.format(nick=target, msg=line))
    return None
