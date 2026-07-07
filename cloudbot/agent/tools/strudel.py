"""Strudel code-music tools for the main agent.

``compose_strudel`` delegates to the ``.strudel`` sub-agent
(``plugins/strudel_agent.py``) so the main ``.agi`` agent can produce a song
from a description. ``strudel_share_link`` is a trivial direct wrapper over the
share endpoint.

The sub-agent module is imported lazily inside the tool body: it imports
``plugins.agent``, which imports this package — importing it at module load
would create a cycle.
"""

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import strudel
from cloudbot.util.web import ServiceError


@tool(
    name="compose_strudel",
    description=(
        "Compose/render a song from a natural-language description or Strudel code "
        "using the cheap code-based music renderer; returns a strudel.cc link and an "
        "audio URL. Use for simple/loopable tracks, explicit Strudel/code requests, or "
        "when Suno is unavailable."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Song description or Strudel code to compose/render",
            }
        },
        "required": ["prompt"],
    },
)
async def compose_strudel(ctx, data):
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return "(error: prompt required)"
    # Lazy import: plugins.strudel_agent → plugins.agent → cloudbot.agent,
    # which eagerly imports this tools package; importing at module load cycles.
    from cloudbot.agent.subagent import SubagentError
    from plugins.strudel_agent import run_strudel

    try:
        return await run_strudel(
            ctx.context.bot, prompt, channel=getattr(ctx.context, "chan", "")
        )
    except strudel.StrudelNotConfigured:
        return "(error: Strudel not configured)"
    except (strudel.StrudelError, SubagentError) as e:
        return f"(error: {e})"


@tool(
    name="strudel_share_link",
    description=(
        "Get a playable strudel.cc share link for a snippet of Strudel code "
        "(no rendering). Use when the user already has/wants Strudel code, not audio."
    ),
    schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Strudel code to share"}
        },
        "required": ["code"],
    },
)
async def strudel_share_link(ctx, data):
    code = str(data.get("code") or "").strip()
    if not code:
        return "(error: code required)"
    try:
        return await run_in_executor(strudel.share_short_url, code)
    except ServiceError:
        return strudel.share_url(code)
