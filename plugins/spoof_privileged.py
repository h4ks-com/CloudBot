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


@hook.command("oper", autohelp=False)
def spoof_oper(text, nick):
    return f"({nick}) cloudia received /oper {text!r} — if you see this in the picker, the client is broken"


@hook.command("identify", autohelp=False)
def spoof_identify(text, nick):
    return f"({nick}) cloudia received /identify {text!r} — if you see this in the picker, the client is broken"


@hook.command("ns", autohelp=False)
def spoof_ns(text, nick):
    return f"({nick}) cloudia received /ns {text!r} — if you see this in the picker, the client is broken"


@hook.command("pass", autohelp=False)
def spoof_pass(text, nick):
    return f"({nick}) cloudia received /pass {text!r} — if you see this in the picker, the client is broken"


@hook.command("kill", autohelp=False)
def spoof_kill(text, nick):
    return f"({nick}) cloudia received /kill {text!r} — if you see this in the picker, the client is broken"
