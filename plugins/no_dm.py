"""Restrict AI-generation commands to public channels — never PMs.

Generation/agent commands (Suno, Gemini, the Hyperframes ``.video`` renderer,
the ``.agi`` agent, Strudel) carry real cost and abuse surface, so they run in
public channels only, mirroring suno's long-standing channel-only rule. One
sieve gates every command from these plugins instead of a per-command check, so
new commands added to them are covered automatically.
"""

from cloudbot import hook
from cloudbot.hook import Priority

CHANNEL_ONLY_PLUGINS = frozenset(
    {"suno", "gemini", "hyperframes", "agent", "strudel_agent"}
)

_CHANNEL_PREFIXES = ("#", "&", "+", "!")


@hook.sieve(priority=Priority.HIGHEST)
def channel_only_sieve(bot, event, _hook):
    if (
        _hook.type != "command"
        or _hook.plugin.title not in CHANNEL_ONLY_PLUGINS
    ):
        return event
    chan = event.chan
    if not chan:
        return event
    if chan != event.nick and chan.startswith(_CHANNEL_PREFIXES):
        return event
    event.notice("That command works in public channels only, not in PMs.")
    return None
