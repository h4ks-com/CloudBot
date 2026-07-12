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
from typing import Any, Literal

Kind = Literal["video", "voice"]

import requests
from agents import Agent, FunctionTool, RunContextWrapper

from cloudbot import hook
from cloudbot.agent.common import parse_args, run_in_executor
from cloudbot.agent.runs import recent_block, record_run
from cloudbot.agent.subagent import SubagentError, run_subagent
from cloudbot.util import hyperframes
from cloudbot.util.typing import (
    start_typing_for_command,
    stop_typing_for_command,
)

logger = logging.getLogger("cloudbot")

_URL_RE = re.compile(r"https?://\S+")
_MP4_RE = re.compile(r"(https?://\S+?\.mp4)")
_AUDIO_RE = re.compile(r"(https?://\S+?\.(?:wav|mp3|m4a|ogg))")
_MEDIA_RE = re.compile(r"https?://\S+?\.(?:mp4|wav|mp3|m4a|ogg)")
_MD_RE = re.compile(r"[*`#]+")

PREAMBLE = (
    "You are CloudBot's video-editor agent. Deliver ONE finished MP4 for the brief, then "
    "stop. The tools and the authoring guide below tell you how to build it. The MOMENT any tool "
    "returns a finished video url, that is your deliverable — post it and stop; never build a "
    "second video on top of one you already have.\n\n"
    "Operating rules:\n"
    "- A render tool returns a job_id; call video_render_status ONCE — it blocks until done "
    "and returns the url. Do NOT poll in a loop.\n"
    "- SYNCED narrated video (each part of the script shows matching footage, e.g. the moon clip "
    "while 'the moon landing' is said): use video_narrated_scenes — pass ordered scenes, each a "
    "`line` plus the `media_id` of footage for that line (download each first), plus optional "
    "music_media_id, lead_in_sec, and voice_reference to clone a voice. It cuts every scene to its "
    "own line's real length so they stay in sync automatically, at any length. Prefer this over "
    "building a video then attaching narration whenever the visuals must track the words. Keep the "
    "scene count to the request's ACTUAL scope: one scene per real beat — a 'short' clip or '3 "
    "moments' is ~3-5 scenes, NOT a dozen. Each scene is a separate cloned narration and they "
    "generate one at a time (~30-60s each), so scene count IS the wait — fewer, punchier scenes is "
    "both faster and usually better. Download exactly one footage clip per scene, not several. Call "
    "video_narrated_scenes EXACTLY ONCE with ALL the scenes in that one call — never call it several "
    "times and never use a render queue; it returns a job_id you poll once. When that job is done, "
    "its url IS the COMPLETE finished video — post it and END YOUR TURN. Do NOT call ANY tool after "
    "it: not video_render / video_render_timeline / video_preview_frame, not another video_tts or "
    "video_add_audio, not a skill, not more downloads. It is finished; anything more just wastes "
    "minutes or rebuilds a slower, worse video.\n"
    "- Narration: read the tts guide (video_skill) first. To FIT a requested length, call "
    "video_tts_estimate with target_sec to get the exact word budget, write the narration to it, "
    "and check the draft with video_tts_estimate(text) before generating — do NOT eyeball the word "
    "count. Generate it in ONE video_tts call (it chunks internally — NEVER split it into multiple "
    "calls or re-run it to adjust length), then size the video to its duration.\n"
    "- Narration + background music: make EXACTLY ONE video_add_audio call passing BOTH "
    "audio_media_id (the narration) AND music_media_id (the music), plus start_sec for the "
    "lead-in. That one call ducks the music under the voice and keeps the music breathing in the "
    "lead-in. NEVER lay the narration and the music in separate video_add_audio calls (that leaves "
    "a silent lead-in or wipes the music).\n"
    "- Report only a url a tool actually returned; NEVER invent one. NEVER claim a length, "
    "clips, or narration you didn't produce — your caption must match the real file.\n"
    "- When a render tool takes a metadata title/description, write them like a real creator "
    "posting the video — catchy and human, never a restatement of the brief, never addressing "
    "the requester by name.\n"
    "- Final answer: ONE short natural caption — single line, no markdown, no emoji, no URLs — "
    "the way a person describes a clip they just made. Don't address the user by name, don't "
    "say 'ready for you', don't sound like an announcer. The bot appends the link itself.\n\n"
)

VOICE_PREAMBLE = (
    "You are CloudBot's voice agent. Produce ONE spoken audio clip for the request with the "
    "video_tts tool, then stop. There is NO video here; the deliverable is a voice clip, so never "
    "build or render a video, and never call video_search_youtube.\n\n"
    "How to do it:\n"
    "- The voice model is ENGLISH-ONLY. Everything you send to video_tts MUST be English prose "
    "(transliterate foreign names into English spelling); any other language comes out as garbled "
    "noise. If the user asks for another language, say you can only do English.\n"
    "- Pass the ENTIRE text to video_tts in ONE call, no matter how long. video_tts handles long "
    "text itself: it splits into chunks and stitches them in the same voice. NEVER split the text "
    "yourself into multiple video_tts calls.\n"
    "- Read the tts authoring skill (video_skill) first. Infer the acting from the request: set "
    "exaggeration (0.3 calm, 0.55 natural, 0.9 dramatic) and cfg_weight (lower, ~0.35, so an intense "
    "line does not rush) to match the emotion, and prep the text (punctuation, short sentences) for "
    "delivery.\n"
    "- The voice performs inline emotion tags in square brackets, placed where the sound happens: "
    "[laugh] [chuckle] [sigh] [gasp] [cough] [breath] [whisper]. Sprinkle one or two where they fit "
    "the delivery (e.g. 'Oh wow [laugh], you did it'); do not tag every line.\n"
    "- To clone a voice: ONLY when the request supplies a clip URL or a media_id, call "
    "video_download_media on it exactly ONCE, then pass that media_id to video_tts as voice_reference "
    "(keep the acting dials for the delivery). If the reference cannot be used (the download errors, "
    "or video_tts rejects it as too short or not usable audio), do NOT retry it, do NOT look for "
    "another clip, and do NOT search YouTube: generate the line in the default voice instead and note "
    "in your caption that the reference clip could not be used.\n"
    "- video_tts is asynchronous: it returns a job_id. Call video_render_status ONCE with that "
    "job_id (it blocks until the audio is ready and returns the url); do NOT poll in a loop.\n\n"
    "Rules:\n"
    "- Report only a url a tool actually returned; never invent one, and never claim words you did "
    "not synthesize.\n"
    "- Final answer: ONE short natural caption (single line, no markdown, no emoji, no URLs). The "
    "bot appends the link itself.\n\n"
)

_STATUS_TOOL = "video_render_status"
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
    """Capture the latest MP4 and audio URLs from a tool result for deterministic reply assembly."""
    mp4 = _MP4_RE.search(text or "")
    if mp4:
        ctx["video_url"] = mp4.group(1)
    audio = _AUDIO_RE.search(text or "")
    if audio:
        ctx["audio_url"] = audio.group(1)


def _wait_for_job(url: str, key: str, job_id: str) -> tuple[str, bool]:
    """Block on video_render_status until the job leaves an active state or the cap
    is hit, so one agent turn covers the whole async render. Runs in an executor
    thread; returns the final ``(text, is_error)``."""
    deadline = time.monotonic() + _STATUS_CAP_S
    text, is_error = hyperframes.call_tool(
        url,
        key,
        _STATUS_TOOL,
        {"job_id": job_id},
        timeout=hyperframes.STATUS_TIMEOUT,
    )
    while time.monotonic() < deadline:
        if is_error or _MEDIA_RE.search(text or ""):
            return text, is_error
        try:
            state = str(json.loads(text).get("state", "")).lower()
        except (ValueError, TypeError):
            state = ""
        if state and state not in hyperframes.ACTIVE_STATES:
            return text, is_error
        time.sleep(_STATUS_INTERVAL_S)
        text, is_error = hyperframes.call_tool(
            url,
            key,
            _STATUS_TOOL,
            {"job_id": job_id},
            timeout=hyperframes.STATUS_TIMEOUT,
        )
    if is_error or _MEDIA_RE.search(text or ""):
        return text, is_error
    return (
        f"(still rendering after {int(_STATUS_CAP_S)}s — job {job_id} did not finish; "
        "no output was produced)",
        True,
    )


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


_agents: dict[tuple[str, str, str], Agent] = {}


async def _get_agent(url: str, key: str, kind: Kind = "video") -> Agent:
    """Build (once per kind) the sub-agent: instructions from the server's authoring
    guides, tools bridged from its MCP. Neither the guides nor the tool definitions
    are duplicated here; the server owns them. ``kind`` picks the preamble: a video
    deliverable (MP4) or a voice one (an audio clip)."""
    cached = _agents.get((url, key, kind))
    if cached is not None:
        return cached
    guides = await run_in_executor(_load_guides, url, key)
    if not guides.strip():
        raise hyperframes.HyperframesError(
            "video-mcp returned no authoring guides"
        )
    specs = await run_in_executor(hyperframes.list_tools, url, key)
    preamble = VOICE_PREAMBLE if kind == "voice" else PREAMBLE
    agent = Agent(
        name=f"VideoCreator-{kind}",
        instructions=preamble + guides,
        tools=_build_tools(url, key, specs),
    )
    _agents[(url, key, kind)] = agent
    return agent


def _run_limits(bot: Any) -> tuple[int, float]:
    cfg = (bot.config.get("plugins") or {}).get("hyperframes_agent") or {}
    return int(cfg.get("max_turns", 40)), float(cfg.get("timeout_s", 600))


def _build_reply(
    text: str, captured: dict[str, str], kind: Kind = "video"
) -> str:
    """One IRC line: caption plus the captured deliverable URL (MP4 for video, the
    audio file for voice). Strips any link the model typed itself (only the
    tool-produced URL is trusted) and flattens markdown.
    """
    url = captured.get("audio_url" if kind == "voice" else "video_url")
    icon = "🎧" if kind == "voice" else "🎬"
    if not url:
        return _URL_RE.sub("", text or "").strip() or "(no result)"
    desc = _URL_RE.sub("", text or "")
    desc = _MD_RE.sub("", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    desc = re.sub(r"[\s:–—-]+$", "", desc).strip()
    if len(desc) > 300:
        desc = desc[:297].rstrip() + "..."
    return f"{icon} {desc} {url}" if desc else f"{icon} {url}"


_REITERATE_NOTE = (
    "Videos you recently made in this channel (newest first). If the user is asking to change, "
    "improve, or riff on one of these, call video_get_recipe on its URL to recover the exact recipe, "
    "tweak the fields, and re-render — do not rebuild from scratch:\n"
)


async def run_hyperframes(
    bot: Any,
    prompt: str,
    channel: str = "",
    effort: hyperframes.Effort | None = None,
    kind: Kind = "video",
) -> str:
    """Create a video via the video-mcp server and return a short result with the
    MP4 URL. Shared by the ``.video`` command and the main agent's ``create_video``
    tool. Recent videos in ``channel`` are surfaced so a follow-up can iterate on them.
    ``effort`` overrides the configured GLM thinking mode for this one run: "fast"
    skips per-turn reasoning, "deep" enables it — a retry escalation, since a single
    reasoning turn can cost minutes. Raises on misconfiguration."""
    url, key = hyperframes.config_from_bot(bot)
    agent = await _get_agent(url, key, kind)
    max_turns, timeout_s = _run_limits(bot)
    cfg = (bot.config.get("plugins") or {}).get("hyperframes_agent") or {}
    model = cfg.get("model") or None
    if effort is not None:
        disable_thinking = effort == "fast"
    else:
        disable_thinking = bool(cfg.get("disable_thinking", False))
    ts = datetime.now().strftime("%H:%M:%S")
    # Only video has a re-render/iterate flow (video_get_recipe); surfacing recent
    # videos to a voice run would inject video-only guidance that contradicts its
    # preamble.
    if kind == "video":
        recent = recent_block(channel, "video")
        context_note = f"{_REITERATE_NOTE}{recent}\n\n" if recent else ""
    else:
        context_note = ""
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
    reply = _build_reply(text, captured, kind)
    out_url = captured.get("audio_url" if kind == "voice" else "video_url")
    if out_url:
        summary = (
            _MD_RE.sub("", _URL_RE.sub("", reply))
            .replace("🎬", "")
            .replace("🎧", "")
            .strip()
        )
        record_run(channel, kind, summary, out_url)
    return reply


_bg_tasks: set[asyncio.Task[None]] = set()

_RENDER_TIMEOUT_S = 2700.0
_video_seq = 0


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


_KIND_NOUN = {"video": "Video", "voice": "Voice"}


async def _post(
    kind: Kind,
    bot: Any,
    conn: Any,
    target: str,
    prompt: str,
    effort: hyperframes.Effort | None = None,
) -> None:
    # The dispatch itself is already announced (the .agi reply / the command line).
    # Here we only keep a typing signal live for the whole background render — the
    # spawning command's own typing stopped when it returned — and post the finished
    # media or a failure.
    noun = _KIND_NOUN[kind]
    global _video_seq
    _video_seq += 1
    typing_id = _video_seq
    await start_typing_for_command(conn, target, typing_id)
    try:
        answer = await asyncio.wait_for(
            run_hyperframes(
                bot, prompt, channel=target, effort=effort, kind=kind
            ),
            timeout=_RENDER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        answer = f"{noun} failed: timed out."
    except hyperframes.HyperframesNotConfigured:
        answer = f"{noun} not configured."
    except (hyperframes.HyperframesError, SubagentError) as e:
        answer = f"{noun} failed: {e}"
    except requests.RequestException as e:
        answer = f"{noun} failed: video-mcp request rejected ({e.__class__.__name__})"
    except Exception:
        logger.exception("hyperframes: unexpected error in _post (%s)", kind)
        answer = f"{noun} failed: unexpected error (see bot logs)"
    finally:
        await stop_typing_for_command(conn, target, typing_id)
    conn.message(target, answer)


def spawn_video(
    bot: Any,
    conn: Any,
    target: str,
    prompt: str,
    effort: hyperframes.Effort | None = None,
) -> None:
    _spawn(_post("video", bot, conn, target, prompt, effort))


@hook.command("video", autohelp=False, allow_private=False)
async def video_command(text, event):
    """<brief> - create a video; renders in the background and posts the MP4 link here when ready."""
    if not text:
        event.reply("usage: .video <what the video should be>")
        return
    event.reply(
        "🎬 On it — putting your video together now; I'll post it here when it's ready (a few minutes)."
    )
    spawn_video(event.bot, event.conn, event.chan, text)


def spawn_voice(
    bot: Any,
    conn: Any,
    target: str,
    prompt: str,
    effort: hyperframes.Effort | None = None,
) -> None:
    _spawn(_post("voice", bot, conn, target, prompt, effort))


@hook.command("speak", autohelp=False, allow_private=False)
async def speak_command(text, event):
    """<what to say> - generate a spoken voice clip (any delivery + voice cloning); posts the audio link here. Include a URL to clone that voice."""
    if not text:
        event.reply(
            "usage: .speak <what to say> (add a direction like 'angrily' or 'terrified whisper', and paste a clip URL to clone that voice)"
        )
        return
    event.reply(
        "🎧 On it, generating the voice now; I'll post the audio here shortly."
    )
    spawn_voice(event.bot, event.conn, event.chan, text)
