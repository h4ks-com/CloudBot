"""Video-creation tool for the main agent.

``create_video`` delegates to the ``.video`` sub-agent
(``plugins/hyperframes.py``) so the main ``.agi`` agent can produce a finished
video from a description and get back a public MP4 URL.

The sub-agent module is imported lazily inside the tool body: it imports
``plugins.agent``, which imports this package — importing it at module load
would create a cycle.
"""

import json
import re
import time
from typing import get_args

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import hyperframes


def _run_analysis(api_url: str, key: str, args: dict[str, object]) -> tuple[str, bool]:
    """Submit video_analyze_static and block until the async job (download + opencv
    pass) finishes, returning the final ``(text, is_error)``."""
    sub, err = hyperframes.call_tool(api_url, key, "video_analyze_static", args)
    if err:
        return sub, True
    match = re.search(r'"job_id"\s*:\s*"([A-Za-z0-9_-]+)"', sub)
    if not match:
        return sub, False
    job_id = match.group(1)
    deadline = time.monotonic() + 300
    text = sub
    while time.monotonic() < deadline:
        text, err = hyperframes.call_tool(
            api_url,
            key,
            "video_render_status",
            {"job_id": job_id},
            timeout=hyperframes.STATUS_TIMEOUT,
        )
        if err:
            return text, True
        try:
            state = str(json.loads(text).get("state", "")).lower()
        except (ValueError, TypeError):
            state = ""
        if state and state not in hyperframes.ACTIVE_STATES:
            return text, False
        time.sleep(3)
    return text, False


@tool(
    name="create_video",
    description=(
        "Create a finished video from a natural-language brief using the Hyperframes "
        "renderer (searches/downloads source clips, composes, renders to MP4). Use for any "
        "'make/create a video' request — presentations/explainers/slideshows, documentaries, "
        "montages, tier-list countdowns, terminal demos, animated charts, math/formula/3D-surface "
        "animations (rendered natively as real animated manim scenes — pass the math in the brief, "
        "no need to draw or fetch pictures of it), or fully custom compositions. Pass the brief as "
        "a clear shape: the most common (text + bg clips + "
        "music) is a SLIDESHOW, not custom HTML — say so in the prompt with concrete segments "
        '(e.g. \'slideshow of 15 scenes ~10s each: scene 1 text "...", scene 2 text "...", '
        "bg clips from nature/landscape YouTube, soundtrack URL <...>'). When you've gathered "
        "actual content (real repo names, real facts), write the segment texts INLINE in the "
        "prompt — don't tell the subagent to 're-fetch' or 'discover' the same data. **For "
        "real-world visuals (avatar, profile screenshot, repo readme, page rendering), call "
        "`browser_screenshot` on the relevant URL FIRST, then include the screenshot URL in "
        "the prompt (e.g. 'use https://.../shot.png as bg for the intro').** The slideshow "
        "tool accepts images as backgrounds. Renders "
        "run in the BACKGROUND like Suno: this returns immediately and the finished MP4 (or a "
        "failure) is posted to the channel automatically when ready. Do NOT wait, do NOT "
        "invent a URL, and NEVER build a webpage or other substitute — a webpage is not a video."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What the video should be — topic, style, length, sources to use.",
            },
            "effort": {
                "type": "string",
                "enum": list(get_args(hyperframes.Effort)),
                "description": (
                    "How much reasoning the video agent spends per step. 'fast' (default) is "
                    "right for most briefs — math renders, clip edits, tier lists, slideshows. "
                    "Pick 'deep' only when a previous fast attempt at the same brief failed or "
                    "came back with warnings — it can be several times slower per step, so it is "
                    "a retry escalation, not a default for hard-looking briefs."
                ),
            },
        },
        "required": ["prompt"],
    },
)
async def create_video(ctx, data):
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return "(error: prompt required)"
    requested = data.get("effort")
    effort = requested if requested in get_args(hyperframes.Effort) else None
    # Lazy import: plugins.hyperframes → plugins.agent → cloudbot.agent, which
    # eagerly imports this tools package; importing at module load cycles.
    from plugins.hyperframes import spawn_video

    event = ctx.context
    target = getattr(event, "chan", None) or getattr(event, "nick", None)
    conn = getattr(event, "conn", None)
    bot = getattr(event, "bot", None)
    if not (bot and conn and target):
        return "(error: no channel context available to post the video)"
    spawn_video(bot, conn, target, prompt, effort)
    # create_video is a stop tool (see plugins/agent.py): the agent run ends here and
    # THIS string is posted verbatim as the reply — the model gets no turn to narrate a
    # video that does not exist yet. Keep it a fixed, honest dispatch acknowledgement.
    return (
        "🎬 On it — putting your video together now; I'll post it here when it's ready "
        "(a few minutes)."
    )


@tool(
    name="video_get_info",
    description=(
        "Inspect a YouTube (or other yt-dlp-supported) video without downloading it: "
        "returns its metadata plus the engagement HEATMAP — the most-replayed moments "
        "(heatmap_peaks, in seconds). Use to answer whether a video has a heatmap and "
        "where its peaks are, or to pick the best moment before making a clip. Read-only."
    ),
    schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "YouTube URL or 11-character video id.",
            }
        },
        "required": ["url"],
    },
)
async def video_get_info(ctx, data):
    url = str(data.get("url") or "").strip()
    if not url:
        return "(error: url required)"
    try:
        api_url, key = hyperframes.config_from_bot(ctx.context.bot)
    except hyperframes.HyperframesNotConfigured:
        return "(error: video tools not configured)"
    text, is_error = await run_in_executor(
        hyperframes.call_tool, api_url, key, "video_get_info", {"url": url}
    )
    if is_error:
        return f"(error: {text})"
    return text or "(no info returned)"


@tool(
    name="video_analyze_static",
    description=(
        "Profile a video for STATIC, structured regions — baked-in subtitles/text, "
        "watermarks, channel logos — so you know which areas are safe to overlay and which "
        "to avoid. Returns avoid-region boxes plus a per-cell avoid/clutter grid in the "
        "source's pixel coordinates. Use to find clear zones for captions/overlays, or to "
        "answer where a video has on-screen text. Read-only; downloads the video, so it can "
        "take up to a few minutes."
    ),
    schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Video URL (any yt-dlp source or direct media link).",
            },
            "fps": {
                "type": "number",
                "description": "Frames sampled per second (default 2).",
            },
            "grid": {
                "type": "integer",
                "description": "Grid resolution NxN (default 4).",
            },
        },
        "required": ["url"],
    },
)
async def video_analyze_static(ctx, data):
    url = str(data.get("url") or "").strip()
    if not url:
        return "(error: url required)"
    try:
        api_url, key = hyperframes.config_from_bot(ctx.context.bot)
    except hyperframes.HyperframesNotConfigured:
        return "(error: video tools not configured)"
    args: dict[str, object] = {"url": url}
    if data.get("fps") is not None:
        args["fps"] = data["fps"]
    if data.get("grid") is not None:
        args["grid"] = data["grid"]
    text, is_error = await run_in_executor(_run_analysis, api_url, key, args)
    if is_error:
        return f"(error: {text})"
    return text or "(no analysis returned)"
