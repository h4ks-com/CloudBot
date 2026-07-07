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

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Any

import requests
from agents import Agent, FunctionTool, RunContextWrapper

from cloudbot import hook
from cloudbot.agent.common import parse_args, run_in_executor
from cloudbot.agent.runs import recent_block, record_run
from cloudbot.agent.subagent import SubagentError, run_subagent
from cloudbot.util import hyperframes

logger = logging.getLogger("cloudbot")

_URL_RE = re.compile(r"https?://\S+")
_MP4_RE = re.compile(r"(https?://\S+?\.mp4)")
_MD_RE = re.compile(r"[*`#]+")

PREAMBLE = (
    "You are CloudBot's video-editor agent. Deliver ONE finished MP4 for the brief, then "
    "stop. The tools and the authoring guide below tell you how to build it.\n\n"
    "Operating rules:\n"
    "- A render tool returns a job_id; call video_render_status ONCE — it blocks until done "
    "and returns the url. Do NOT poll in a loop.\n"
    "- Report only a url a tool actually returned; NEVER invent one. NEVER claim a length, "
    "clips, or narration you didn't produce — your caption must match the real file.\n"
    "- When a render tool takes a metadata title/description, write them like a real creator "
    "posting the video — catchy and human, never a restatement of the brief, never addressing "
    "the requester by name.\n"
    "- Final answer: ONE short natural caption — single line, no markdown, no emoji, no URLs — "
    "the way a person describes a clip they just made. Don't address the user by name, don't "
    "say 'ready for you', don't sound like an announcer. The bot appends the link itself.\n\n"
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
    """Capture the latest MP4 URL from a tool result for deterministic reply assembly."""
    mp4 = _MP4_RE.search(text or "")
    if mp4:
        ctx["video_url"] = mp4.group(1)


def _wait_for_job(url: str, key: str, job_id: str) -> tuple[str, bool]:
    """Block on video_render_status until the job leaves an active state or the cap
    is hit, so one agent turn covers the whole async render. Runs in an executor
    thread; returns the final ``(text, is_error)``."""
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
            # A single tool error must not abort the whole sub-agent run; return the
            # error as plain text so the model can read it and route around.
            try:
                if _name == _STATUS_TOOL:
                    text, is_error = await run_in_executor(
                        _wait_for_job, url, key, str(args.get("job_id") or "")
                    )
                else:
                    text, is_error = await run_in_executor(
                        hyperframes.call_tool, url, key, _name, args
                    )
            except hyperframes.HyperframesError as e:
                return f"(tool error: {e})"
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
    cfg = (bot.config.get("plugins") or {}).get("hyperframes_agent") or {}
    return int(cfg.get("max_turns", 40)), float(cfg.get("timeout_s", 600))


def _build_reply(text: str, captured: dict[str, str]) -> str:
    """One IRC line: caption plus the captured MP4 URL. Strips any link the model
    typed itself (only the tool-produced URL is trusted) and flattens markdown.
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


_REITERATE_NOTE = (
    "Videos you recently made in this channel (newest first). If the user is asking to change, "
    "improve, or riff on one of these, call video_get_recipe on its URL to recover the exact recipe, "
    "tweak the fields, and re-render — do not rebuild from scratch:\n"
)


async def run_hyperframes(bot: Any, prompt: str, channel: str = "") -> str:
    """Create a video via the video-mcp server and return a short result with the
    MP4 URL. Shared by the ``.video`` command and the main agent's ``create_video``
    tool. Recent videos in ``channel`` are surfaced so a follow-up can iterate on them.
    Raises on misconfiguration."""
    url, key = hyperframes.config_from_bot(bot)
    agent = await _get_agent(url, key)
    max_turns, timeout_s = _run_limits(bot)
    cfg = (bot.config.get("plugins") or {}).get("hyperframes_agent") or {}
    model = cfg.get("model") or None
    disable_thinking = bool(cfg.get("disable_thinking", False))
    ts = datetime.now().strftime("%H:%M:%S")
    recent = recent_block(channel, "video")
    context_note = f"{_REITERATE_NOTE}{recent}\n\n" if recent else ""
    enriched = f"{context_note}[time: {ts}]\n{prompt}"
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
    reply = _build_reply(text, captured)
    video_url = captured.get("video_url")
    if video_url:
        summary = (
            _MD_RE.sub("", _MP4_RE.sub("", reply)).replace("🎬", "").strip()
        )
        record_run(channel, "video", summary, video_url)
    return reply


_bg_tasks: set[asyncio.Task[None]] = set()

_RENDER_TIMEOUT_S = 2700.0


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _post_video(bot: Any, conn: Any, target: str, prompt: str) -> None:
    try:
        answer = await asyncio.wait_for(
            run_hyperframes(bot, prompt, channel=target),
            timeout=_RENDER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        answer = "Video failed: render timed out."
    except hyperframes.HyperframesNotConfigured:
        answer = "Video creation not configured."
    except (hyperframes.HyperframesError, SubagentError) as e:
        answer = f"Video failed: {e}"
    except requests.RequestException as e:
        answer = f"Video failed: video-mcp unreachable ({e.__class__.__name__})"
    except Exception:
        logger.exception("hyperframes: unexpected error in _post_video")
        answer = "Video failed: unexpected error (see bot logs)"
    conn.message(target, answer)


def spawn_video(bot: Any, conn: Any, target: str, prompt: str) -> None:
    _spawn(_post_video(bot, conn, target, prompt))


@hook.command("video", autohelp=False, allow_private=False)
async def video_command(text, event):
    """<brief> - create a video; renders in the background and posts the MP4 link here when ready."""
    if not text:
        event.reply("usage: .video <what the video should be>")
        return
    event.reply(
        "🎬 Working on your video — I'll post it here when it's ready (a few minutes)."
    )
    spawn_video(event.bot, event.conn, event.chan, text)
