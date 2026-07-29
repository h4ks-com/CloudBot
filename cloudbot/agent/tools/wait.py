"""Sleep, so slow work can be waited on without spending a model call per check.

Anything that takes minutes -- a render, a transcription, a notebook run, a deploy, a rate
limit clearing -- otherwise leaves only bad options: check in a tight loop and burn a call
every time, or have the service hold its HTTP response open, which puts the wait behind every
proxy on the path and fails in ways that look like the service being broken.

Sleeping here costs one model call per wait, whatever is being waited for.
"""

import asyncio

from cloudbot.agent.registry import tool

# A single wait must not swallow the whole run: the agent needs turns left to act on what it
# finds, and something that failed early should be noticed rather than slept through.
MAX_SECONDS = 300.0
MIN_SECONDS = 1.0


@tool(
    name="wait",
    description=(
        "Sleep for a while before trying or checking something again. Use it whenever work "
        "takes longer than a moment and you would otherwise poll. Pass the best estimate you "
        "have of the time left. Sleeps at most 300s per call, so for longer waits alternate "
        "wait and check. Always do something between waits; never chain them back to back, "
        "and never use it to pause for its own sake."
    ),
    schema={
        "type": "object",
        "properties": {
            "seconds": {
                "type": "number",
                "description": "How long to sleep, 1-300. Use the time remaining if something reports one.",
            },
            "reason": {
                "type": "string",
                "description": "What is being waited for, for the log.",
            },
        },
        "required": ["seconds"],
    },
    wrap_errors=True,
)
async def wait(ctx, data) -> str:
    seconds = min(max(float(data.get("seconds", MIN_SECONDS)), MIN_SECONDS), MAX_SECONDS)
    await asyncio.sleep(seconds)
    reason = str(data.get("reason") or "").strip()
    return f"Waited {seconds:.0f}s{f' for {reason}' if reason else ''}. Check now."
