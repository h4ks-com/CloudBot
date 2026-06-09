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
    "You are CloudBot's video-creation sub-agent. Produce ONE finished video for the "
    "user's brief, then stop.\n\n"
    "Pick the simplest tool that fits:\n"
    "- Prefer the ready templates — video_render_terminal, video_render_chart, "
    "video_render_tierlist — and catalog blocks (video_catalog then video_render_block). "
    "They need no HTML and are reliable.\n"
    "- Author raw HTML with video_render / video_render_timeline ONLY when no template "
    "fits. First read the relevant video_skill docs, then video_lint your HTML and FIX "
    "every reported error until lint passes — never render HTML that fails lint.\n\n"
    "Rendering: a render tool returns a job_id. Call video_render_status ONCE with that "
    "job_id — it blocks until the render finishes and returns the final MP4 url. Do NOT "
    "poll in a loop. Report only a url that came back from a tool; never invent one.\n\n"
    "Final answer: ONE short plain-text caption (a single line, no markdown, no emoji, "
    "no URLs) describing the video — the bot appends the link itself.\n\n"
)

_STATUS_TOOL = "video_render_status"
_ACTIVE_STATES = {"queued", "running", "pending", "in_progress", "processing"}
_STATUS_CAP_S = 540.0
_STATUS_INTERVAL_S = 4.0


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
            if _name == _STATUS_TOOL:
                text, is_error = await run_in_executor(
                    _wait_for_job, url, key, str(args.get("job_id") or "")
                )
            else:
                text, is_error = await run_in_executor(
                    hyperframes.call_tool, url, key, _name, args
                )
            if not is_error and isinstance(ctx.context, dict):
                _capture_urls(ctx.context, text)
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
