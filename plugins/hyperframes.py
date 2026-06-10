"""Video-creation sub-agent for CloudBot (Hyperframes renderer).

  .video <brief>  — create a finished video from a natural-language brief via
                    the video-mcp server and reply with a public MP4 URL.

The video know-how lives in the video-mcp server, not here: the sub-agent's
instructions are the server's authoring guides (its MCP resources) and its
tools are whatever the server's MCP exposes (search/download/render/status/
analyze), bridged to FunctionTools. What stays client-side is the deterministic
reply assembly — models corrupt URLs, so the MP4 link is captured from the tool
output and never taken from the model's prose.

``run_hyperframes`` is exported so the main ``.agi`` agent can delegate via the
``create_video`` tool.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

from agents import Agent, FunctionTool, RunContextWrapper

from cloudbot import hook
from cloudbot.agent.common import parse_args, run_in_executor
from cloudbot.agent.subagent import SubagentError, run_subagent
from cloudbot.util import hyperframes
from cloudbot.util.typing import (
    start_typing_for_command,
    stop_typing_for_command,
)

_URL_RE = re.compile(r"https?://\S+")
_MP4_RE = re.compile(r"(https?://\S+?\.mp4)")
_MD_RE = re.compile(r"[*`#]+")

PREAMBLE = (
    "You are CloudBot's video-editor agent. You edit video with code: you can find "
    "footage, cut it, loop it, stack clips, and composite text/graphics/animation over "
    "video, then render one finished MP4. Deliver ONE finished video for the brief, then "
    "stop.\n\n"
    "Workflow:\n"
    "1. Footage — when the brief names a source (a YouTube link, 'the part where he says "
    "X'), use video_search / video_get_info / video_search_subtitles to find the exact "
    "window, then download only that window. Skip this for purely generated videos.\n"
    "2. Edit — chain operations by media_id: each edit returns a new media_id you feed to "
    "the next step. video_loop repeats a clip; video_caption burns timed text onto a clip. "
    "Build the clip first, then add text/overlays.\n"
    "3. Text & composite — to put plain timed subtitles over a clip (e.g. loop a moment and "
    "talk to the viewer with rotating lines), use video_caption: one fast ffmpeg pass, no "
    "HTML. Use an HTML composition only for animated or graphic overlays: prefer a ready "
    "template (video_render_terminal, video_render_chart, video_render_tierlist) or a "
    "catalog block (video_catalog then video_render_block); author raw HTML with "
    "video_render / video_render_timeline ONLY when no template fits — read the relevant "
    "video_skill docs first, then video_lint and fix every ERROR (warnings are fine) until "
    "lint passes. Never render HTML that fails lint.\n"
    "4. Render — a render tool returns a job_id. Call video_render_status ONCE with it; it "
    "blocks until done and returns the MP4 url. Do NOT poll in a loop.\n\n"
    "Efficiency — you are judged on getting it right in the fewest steps:\n"
    "- Author the composition ONCE. Do not rewrite it to 'improve' a video that already "
    "rendered.\n"
    "- Render ONCE. After you have an MP4 url, you are done — stop, do not re-render.\n"
    "- This server IS the compositor: there is no external subtitle/caption service. Draw "
    "text and graphics as HTML over the video yourself.\n"
    "- Report only a url that a tool returned. NEVER invent or guess a url.\n\n"
    "When a render tool takes a metadata title/description, write them like a real creator "
    "posting the video — catchy and human, never a restatement of the brief and never "
    "addressing the requester by name.\n\n"
    "Final answer: ONE short, natural caption — a single line, no markdown, no emoji, no "
    "URLs — the way a person would describe the clip they just made. Don't address the user "
    "by name, don't say things like 'ready for you', don't sound like an announcer. The bot "
    "appends the link itself.\n\n"
)

_STATUS_TOOL = "video_render_status"
_ACTIVE_STATES = {"queued", "running", "pending", "in_progress", "processing"}
_STATUS_CAP_S = 540.0
_STATUS_INTERVAL_S = 4.0

# Tools that submit a chrome render (return a job_id). One finished render answers
# any brief, so the second one in a run is the model re-authoring/re-rendering to
# "improve" an already-done video — minutes of wasted work. The guard below admits
# the first and short-circuits the rest. video_loop is intentionally excluded: it is
# a cheap ffmpeg edit returning a chainable media_id, not a final render.
_FINAL_RENDER = {
    "video_render",
    "video_render_timeline",
    "video_render_block",
    "video_render_terminal",
    "video_render_chart",
    "video_render_tierlist",
}
_JOB_RE = re.compile(r'"job_id"\s*:\s*"([^"]+)"')


def _clean_schema(schema: Any) -> dict[str, Any]:
    """An MCP inputSchema as a FunctionTool params schema — drop the $schema key
    the JSON-Schema generator adds, which the SDK's tool schema doesn't expect.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    return {k: v for k, v in schema.items() if k != "$schema"} or {
        "type": "object",
        "properties": {},
    }


def _capture_urls(ctx: dict[str, str], text: str) -> None:
    """Record the latest MP4 URL seen in a tool result, so the reply uses the
    exact link the renderer produced instead of the model's retype."""
    mp4 = _MP4_RE.search(text or "")
    if mp4:
        ctx["video_url"] = mp4.group(1)


def _wait_for_job(url: str, key: str, job_id: str) -> tuple[str, bool]:
    """Poll video_render_status until the job leaves an active state (or the cap is
    hit), so one agent call covers a whole async render — no per-turn poll loop that
    burns the agent's turn/time budget while the render takes minutes. Runs in an
    executor thread; returns the final ``(text, is_error)``."""
    deadline = time.monotonic() + _STATUS_CAP_S
    text, is_error = hyperframes.call_tool(
        url, key, _STATUS_TOOL, {"job_id": job_id}
    )
    while time.monotonic() < deadline:
        if is_error or _MP4_RE.search(text or ""):
            return text, is_error
        try:
            state = str(json.loads(text).get("state", "")).lower()
        except (ValueError, TypeError):
            state = ""
        if state and state not in _ACTIVE_STATES:
            return text, is_error
        time.sleep(_STATUS_INTERVAL_S)
        text, is_error = hyperframes.call_tool(
            url, key, _STATUS_TOOL, {"job_id": job_id}
        )
    return text, is_error


def _build_tools(
    url: str, key: str, specs: list[dict[str, Any]]
) -> list[FunctionTool]:
    """Bridge each tool video-mcp exposes to a FunctionTool. Generic, so new
    server tools appear automatically; every result is scanned for the final MP4
    and metadata URLs, captured into the run context for deterministic replies.
    """
    tools: list[FunctionTool] = []
    for spec in specs:
        name = spec.get("name")
        if not name:
            continue

        async def on_invoke(
            ctx: RunContextWrapper, args_json: str, _name: str = name
        ) -> str:
            args = parse_args(args_json)
            ctxd = ctx.context if isinstance(ctx.context, dict) else None
            if (
                _name in _FINAL_RENDER
                and ctxd is not None
                and ctxd.get("final_render_submitted")
            ):
                done = ctxd.get("video_url")
                if done:
                    return (
                        f"Already rendered: {done}\nSTOP — the video is finished. "
                        "Do not render again; reply with your one-line caption now."
                    )
                return (
                    "A render was already submitted this run. Call video_render_status "
                    "with the existing job_id; do not submit another render."
                )
            if _name == _STATUS_TOOL:
                text, is_error = await run_in_executor(
                    _wait_for_job, url, key, str(args.get("job_id") or "")
                )
            else:
                text, is_error = await run_in_executor(
                    hyperframes.call_tool, url, key, _name, args
                )
            if not is_error and ctxd is not None:
                _capture_urls(ctxd, text)
                if _name in _FINAL_RENDER and _JOB_RE.search(text or ""):
                    ctxd["final_render_submitted"] = True
            return text or "(no result)"

        tools.append(
            FunctionTool(
                name=name,
                description=spec.get("description", ""),
                params_json_schema=_clean_schema(spec.get("inputSchema")),
                on_invoke_tool=on_invoke,
            )
        )
    return tools


def _load_guides(url: str, key: str) -> str:
    """Concatenate every authoring guide the server publishes as the agent's
    instructions; fall back to the master guide if the listing is empty."""
    texts: list[str] = []
    for res in hyperframes.list_resources(url, key):
        uri = res.get("uri")
        if uri:
            texts.append(hyperframes.read_resource(url, key, uri))
    if not any(t.strip() for t in texts):
        texts = [hyperframes.read_resource(url, key, "guide://authoring")]
    return "\n\n".join(t for t in texts if t)


class _AgentState:
    agent: Agent | None = None
    api: tuple[str, str] | None = None


async def _get_agent(url: str, key: str) -> Agent:
    """Build (once) the VideoCreator agent: instructions from the server's
    authoring guides, tools bridged from its MCP. Neither the guides nor the
    tool definitions are duplicated here — the server owns them."""
    if _AgentState.agent is not None and _AgentState.api == (url, key):
        return _AgentState.agent
    guides = await run_in_executor(_load_guides, url, key)
    if not guides.strip():
        raise hyperframes.HyperframesError(
            "video-mcp returned no authoring guides"
        )
    specs = await run_in_executor(hyperframes.list_tools, url, key)
    agent = Agent(
        name="VideoCreator",
        instructions=PREAMBLE + guides,
        tools=_build_tools(url, key, specs),
    )
    _AgentState.agent = agent
    _AgentState.api = (url, key)
    return agent


def _run_limits(bot: Any) -> tuple[int, float]:
    # A video run is search → download → compose → render → poll, so it needs
    # more turns and a longer ceiling than the audio agents; renders alone take
    # minutes. Cap so a looping model can't run forever.
    cfg = (bot.config.get("plugins") or {}).get("hyperframes_agent") or {}
    return int(cfg.get("max_turns", 40)), float(cfg.get("timeout_s", 600))


def _build_reply(text: str, captured: dict[str, str]) -> str:
    """One clean IRC line: the model's caption plus the exact MP4 URL the tool
    produced. Any link the model wrote itself is stripped (models retype URLs and
    corrupt them); markdown and newlines are flattened so it reads as one line.
    """
    video = captured.get("video_url")
    if not video:
        return text or "(no result)"
    desc = _URL_RE.sub("", text or "")
    desc = _MD_RE.sub("", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    desc = re.sub(r"[\s:–—-]+$", "", desc).strip()
    if len(desc) > 300:
        desc = desc[:297].rstrip() + "..."
    return f"🎬 {desc} {video}" if desc else f"🎬 {video}"


async def run_hyperframes(bot: Any, prompt: str) -> str:
    """Create a video via the video-mcp server and return a short result with the
    MP4 URL. Shared by the ``.video`` command and the main agent's ``create_video``
    tool. Raises on misconfiguration."""
    url, key = hyperframes.config_from_bot(bot)
    agent = await _get_agent(url, key)
    max_turns, timeout_s = _run_limits(bot)
    cfg = (bot.config.get("plugins") or {}).get("hyperframes_agent") or {}
    model = cfg.get("model") or None
    disable_thinking = bool(cfg.get("disable_thinking", False))
    ts = datetime.now().strftime("%H:%M:%S")
    enriched = f"[time: {ts}]\n{prompt}"
    captured: dict[str, str] = {}
    text = await run_subagent(
        bot,
        agent=agent,
        prompt=enriched,
        max_turns=max_turns,
        timeout_s=timeout_s,
        context=captured,
        model=model,
        disable_thinking=disable_thinking,
    )
    return _build_reply(text, captured)


@hook.command("video", autohelp=False)
async def video_command(text, event):
    """<brief> - create a video for the brief via the Hyperframes renderer; returns an MP4 link."""
    if not text:
        event.reply("usage: .video <what the video should be>")
        return
    event.reply("Creating video — this can take a few minutes...")
    typing_id = id(event)
    target = event.chan or event.nick
    await start_typing_for_command(event.conn, target, typing_id)
    try:
        answer = await run_hyperframes(event.bot, text)
    except hyperframes.HyperframesNotConfigured:
        event.reply("Video creation not configured.")
        return
    except (hyperframes.HyperframesError, SubagentError) as e:
        event.reply(f"Video agent failed: {e}")
        return
    finally:
        await stop_typing_for_command(event.conn, target, typing_id)
    event.reply(answer)
