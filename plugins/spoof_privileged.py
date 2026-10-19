"""Test bait for the bot-tools spoofed-privileged-command filter.

Registers a handful of slash names that the client's denylist should
strip at ingest (so they never appear in the slash picker) and refuse
to dispatch even if they did (defence in depth in useMessageSending).

These commands do nothing useful; they exist so a tester can confirm
that:
  - the bot DOES advertise them in its +draft/bot-cmds payload
  - the client DOES NOT show them in the slash picker
  - a user typing /oper or /identify never produces a +draft/bot-cmd
    TAGMSG aimed at this bot
"""

from typing import Annotated

from cloudbot import hook
from cloudbot.clients.irc import IrcClient
from cloudbot.hook import Arg

_BROKEN = "if you see this in the picker, the client is broken"

ArgsText = Annotated[str | None, Arg("Anything, it is ignored")]


@hook.command("oper", autohelp=False)
def spoof_oper(nick: str, conn: IrcClient, args: ArgsText = None):
    """Test bait that the client must hide"""
    return f"({nick}) {conn.nick} received /oper {args or ''!r} — {_BROKEN}"


@hook.command("identify", autohelp=False)
def spoof_identify(nick: str, conn: IrcClient, args: ArgsText = None):
    """Test bait that the client must hide"""
    return f"({nick}) {conn.nick} received /identify {args or ''!r} — {_BROKEN}"


@hook.command("ns", autohelp=False)
def spoof_ns(nick: str, conn: IrcClient, args: ArgsText = None):
    """Test bait that the client must hide"""
    return f"({nick}) {conn.nick} received /ns {args or ''!r} — {_BROKEN}"


@hook.command("pass", autohelp=False)
def spoof_pass(nick: str, conn: IrcClient, args: ArgsText = None):
    """Test bait that the client must hide"""
    return f"({nick}) {conn.nick} received /pass {args or ''!r} — {_BROKEN}"


@hook.command("kill", autohelp=False)
def spoof_kill(nick: str, conn: IrcClient, args: ArgsText = None):
    """Test bait that the client must hide"""
    return f"({nick}) {conn.nick} received /kill {args or ''!r} — {_BROKEN}"
