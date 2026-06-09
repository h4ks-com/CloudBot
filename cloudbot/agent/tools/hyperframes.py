"""Video-creation tool for the main agent.

``create_video`` delegates to the ``.video`` sub-agent
(``plugins/hyperframes.py``) so the main ``.agi`` agent can produce a finished
video from a description and get back a public MP4 URL.

The sub-agent module is imported lazily inside the tool body: it imports
``plugins.agent``, which imports this package — importing it at module load
would create a cycle.
"""

from cloudbot.agent.registry import tool
from cloudbot.util import hyperframes


@tool(
    name="create_video",
    description=(
        "Create a finished video from a natural-language brief using the Hyperframes "
        "renderer (searches/downloads source clips as needed, composes, renders to MP4). "
        "Returns a public MP4 URL. Use for any 'make/create a video' request — tier-list "
        "countdowns, terminal demos, animated charts, or fully custom compositions. "
        "Renders take a few minutes."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What the video should be — topic, style, length, sources to use.",
            }
        },
        "required": ["prompt"],
    },
)
async def create_video(ctx, data):
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return "(error: prompt required)"
    # Lazy import: plugins.hyperframes → plugins.agent → cloudbot.agent, which
    # eagerly imports this tools package; importing at module load cycles.
    from cloudbot.agent.subagent import SubagentError
    from plugins.hyperframes import run_hyperframes

    try:
        return await run_hyperframes(ctx.context.bot, prompt)
    except hyperframes.HyperframesNotConfigured:
        return "(error: video creation not configured)"
    except (hyperframes.HyperframesError, SubagentError) as e:
        return f"(error: {e})"
