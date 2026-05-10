"""Direct .webxdc / .inline_app commands.

Thin wrapper around the agent runner. Forces the model down the webxdc_app
tool path so casual `.webxdc create poll with X Y Z` calls always produce
an inline app rather than a chat reply or generic web_app deploy.

Underlying tool lives in cloudbot.agent.tools.webxdc; the agent picks it
naturally via .agi too — this command is just a shortcut + bias.
"""

import logging

from cloudbot import hook

logger = logging.getLogger("cloudbot")

# Prompt template biases the model toward webxdc_app. We restate the rules
# here so the bias survives even if the system prompt is truncated under load.
_BIAS_TEMPLATE = (
    "The user wants an inline interactive widget rendered in chat. You MUST "
    "respond by calling the webxdc_app tool — do not reply with plain text, "
    "do not call web_app or paste_markdown. Generate a self-contained HTML "
    "document that uses window.webxdc.sendUpdate / setUpdateListener for "
    "multi-user state sync, then call webxdc_app with the html, a short "
    "name, and an optional description. Final assistant message should be "
    "ONLY the URL the tool returned (one line, no extra commentary).\n\n"
    "User request: {text}"
)


@hook.command("webxdc", "inline_app", "appchat", autohelp=False)
async def webxdc_command(text, event):
    """<description> - generate and post an inline webxdc app (poll, todo, mini-game, …)."""
    if not text:
        event.reply(
            "usage: .webxdc <app description>  "
            "(e.g. '.webxdc poll: pizza, sushi, tacos')"
        )
        return

    chan = getattr(event, "chan", "") or ""
    if not chan.startswith(("#", "&", "+", "!")) or chan == event.nick:
        event.reply(
            "Webxdc apps only available in public channels, not private messages."
        )
        return

    # Reuse the agent's full pipeline (backend selection, retries, paste-on-fail
    # reporting, capture event, etc.). Importing from plugins.agent works since
    # CloudBot's plugin loader exposes plugins as a regular package on sys.path.
    from plugins.agent import _run_agent

    biased = _BIAS_TEMPLATE.format(text=text)
    await _run_agent(event, biased)
