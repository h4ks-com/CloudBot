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
from plugins.core import bot_cmds

Kind = Literal["video", "voice"]


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
    "- SYNCED narrated video / explainer, footage OR math (this is ALSO how you make a math/formula "
    "explainer. Do NOT hand-build one from video_graphic + video_add_audio): download the "
    "footage and music you need first (media_ids), then author a composition JSON (the full "
    "preset/schema is in the video_compose tool description; don't guess it). A composition is "
    'tracks of clips: each scene is a `composition` clip (duration "fit") holding its own tracks, '
    "one visual (footage media_id, or a math/graphic clip), at most one `voice` clip narrating it, "
    "and at most one `caption` clip. Music is a single `audio` clip on its own top-level track, not "
    "inside a scene. Caption and other styles cascade from `defaults`, so set "
    "`defaults.caption.mode` / `.color` once there and only override per scene when it must differ. "
    "Call video_plan on the composition first (it is free and instant) and read the findings it "
    "returns (`[{path,severity,message,hint}]`); fix EVERY error finding and re-plan until it comes "
    "back clean before rendering anything. Captions stay automatic and word-synced: caption mode "
    "'karaoke' highlights each word as it is spoken (great for shorts/reels, pick a bright color "
    "like 'yellow' or 'cyan'); the default phrase-cue mode reads better for longer lines. Caption "
    "style also takes background ('box' translucent panel, 'blur' frosted strip, 'none' text only), "
    "plus shadow and outline (both on by default for legibility): put 'blur' or 'box' behind "
    "captions over uncontrolled YouTube footage (which may be bright or busy), but use 'none' over "
    "a math/graphic scene, whose dark background already reads text cleanly. Keep "
    "each scene's visual (especially a math/graph) centered so it never reaches into the bottom "
    "caption band. Output defaults to landscape resolution (pick portrait ONLY for a short/reel). "
    "Prefer this composition flow over building a video then attaching narration afterward, "
    "whenever the visuals must track the words. Keep scene count to the request's ACTUAL scope: "
    "one scene per real beat. A 'short' clip or '3 moments' is ~3-5 scenes, NOT a dozen. Each "
    "scene's narration generates separately, so scene count IS the wait: fewer, "
    "punchier scenes is both faster and usually better. Voice synthesis is the single biggest "
    "cost and it scales DIRECTLY with word count (a few seconds per spoken second), so keep each "
    "scene's narration to one or two tight sentences: verbose lines are the main thing that makes "
    "a video slow. In this composition flow do NOT call video_tts_estimate (that is only for the "
    "standalone video_tts path), and skip video_get_info heatmaps for calm narration b-roll: "
    "search, pick ONE clip per scene, download it, and move straight to video_plan. Download "
    "exactly one footage clip per scene, not several. Once video_plan is clean, call video_compose EXACTLY ONCE with the full "
    "composition (never call it more than once, and never use a render queue); it returns a "
    "job_id you poll once. When video_render_status returns a url for that job, that IS the "
    "COMPLETE finished video: post it and END YOUR TURN. Do NOT call ANY tool after it: not "
    "video_render / video_render_timeline / video_preview_frame, not another video_tts or "
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
    "the requester by name. Also pass metadata.brief = the user's request VERBATIM, so the "
    "stored project file keeps what the video was asked to be.\n"
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

# Tools that build the entire video in one call — re-invoking them just re-runs the slow TTS. Once
# dispatched in a run, repeats are refused (see on_invoke) so the model polls + posts instead.
_ONE_SHOT_BUILDERS = {"video_compose"}


def _job_id_from(text: str) -> str:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ""
    job = parsed.get("job_id") if isinstance(parsed, dict) else None
    return job if isinstance(job, str) else ""


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
            # A one-shot builder renders the WHOLE video in one call (each call re-runs the slow
            # per-line TTS). The model sometimes re-renders it several times instead of posting the
            # first result; once it is dispatched, refuse repeats and point back at the pending job.
            ctxd = ctx.context if isinstance(ctx.context, dict) else None
            if _name in _ONE_SHOT_BUILDERS and ctxd is not None:
                pending = ctxd.get("_builder_job")
                if pending:
                    return (
                        f"({_name} was already dispatched as job {pending}. Do NOT build it again — "
                        f'call {_STATUS_TOOL} with job_id "{pending}" ONCE, then post that url and '
                        "end your turn.)"
                    )
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
                if _name in _ONE_SHOT_BUILDERS:
                    job = _job_id_from(text)
                    if job:
                        ctx.context["_builder_job"] = job
            # A failed build must not keep the one-shot lock: when the pending builder job's
            # status comes back as an error, clear it so the model may fix and dispatch again.
            if _name == _STATUS_TOOL and isinstance(ctx.context, dict):
                pending = ctx.context.get("_builder_job")
                polled = str(args.get("job_id") or "")
                if (
                    pending
                    and polled == pending
                    and (is_error or '"state": "error"' in text)
                ):
                    ctx.context.pop("_builder_job", None)
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
    on_tool_step: Any = None,
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
        on_tool_step=on_tool_step,
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
    trigger_msgid: str | None = None,
) -> None:
    # The dispatch itself is already announced (the .agi reply / the command line).
    # Here we keep a typing signal live for the whole background render, stream the
    # render's tool calls as a draft/bot-tools workflow, and post the finished media
    # (or a failure) carrying the workflow's terminal so its card lands on this reply.
    noun = _KIND_NOUN[kind]
    global _video_seq
    _video_seq += 1
    typing_id = _video_seq
    await start_typing_for_command(conn, target, typing_id)
    workflow_id = bot_cmds.start_tool_workflow(
        conn, target, kind, trigger_msgid
    )
    failed = False
    try:
        answer = await asyncio.wait_for(
            run_hyperframes(
                bot,
                prompt,
                channel=target,
                effort=effort,
                kind=kind,
                on_tool_step=bot_cmds.tool_step_sink(conn, target, workflow_id),
            ),
            timeout=_RENDER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        answer = f"{noun} failed: timed out."
        failed = True
    except hyperframes.HyperframesNotConfigured:
        answer = f"{noun} not configured."
        failed = True
    except (hyperframes.HyperframesError, SubagentError) as e:
        answer = f"{noun} failed: {e}"
        failed = True
    except requests.RequestException as e:
        answer = f"{noun} failed: video-mcp request rejected ({e.__class__.__name__})"
        failed = True
    except Exception:
        logger.exception("hyperframes: unexpected error in _post (%s)", kind)
        answer = f"{noun} failed: unexpected error (see bot logs)"
        failed = True
    finally:
        await stop_typing_for_command(conn, target, typing_id)
    conn.message(
        target,
        answer,
        tags=bot_cmds.workflow_terminal_tag(
            workflow_id, "failed" if failed else "complete"
        ),
    )


def spawn_video(
    bot: Any,
    conn: Any,
    target: str,
    prompt: str,
    effort: hyperframes.Effort | None = None,
    trigger_msgid: str | None = None,
) -> None:
    _spawn(_post("video", bot, conn, target, prompt, effort, trigger_msgid))


@hook.command("video", autohelp=False, allow_private=False)
async def video_command(text, event):
    """<brief> - create a video; renders in the background and posts the MP4 link here when ready."""
    if not text:
        event.reply("usage: .video <what the video should be>")
        return
    event.reply(
        "🎬 On it — putting your video together now; I'll post it here when it's ready (a few minutes)."
    )
    spawn_video(
        event.bot,
        event.conn,
        event.chan,
        text,
        trigger_msgid=event.tag_value("msgid"),
    )


def spawn_voice(
    bot: Any,
    conn: Any,
    target: str,
    prompt: str,
    effort: hyperframes.Effort | None = None,
    trigger_msgid: str | None = None,
) -> None:
    _spawn(_post("voice", bot, conn, target, prompt, effort, trigger_msgid))


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
    spawn_voice(
        event.bot,
        event.conn,
        event.chan,
        text,
        trigger_msgid=event.tag_value("msgid"),
    )
