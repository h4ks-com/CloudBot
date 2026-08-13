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

from cloudbot import hook

_BROKEN = "if you see this in the picker, the client is broken"


@hook.command("oper", autohelp=False)
def spoof_oper(text, nick, conn):
    return f"({nick}) {conn.nick} received /oper {text!r} — {_BROKEN}"


@hook.command("identify", autohelp=False)
def spoof_identify(text, nick, conn):
    return f"({nick}) {conn.nick} received /identify {text!r} — {_BROKEN}"


@hook.command("ns", autohelp=False)
def spoof_ns(text, nick, conn):
    return f"({nick}) {conn.nick} received /ns {text!r} — {_BROKEN}"


@hook.command("pass", autohelp=False)
def spoof_pass(text, nick, conn):
    return f"({nick}) {conn.nick} received /pass {text!r} — {_BROKEN}"
