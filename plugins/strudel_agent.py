"""Strudel composition sub-agent for CloudBot.

  .strudel <prompt>  — compose a short song from a description, render it via
                       the rendel MCP server, and reply with a strudel.cc share
                       link plus the rendered audio URL.

The Strudel domain knowledge lives in the rendel MCP server, not here: the
sub-agent's instructions come from rendel's ``compose_strudel`` prompt and its
tools are whatever rendel's MCP exposes (render/search/sounds/share), bridged to
FunctionTools. What stays client-side is the deterministic reply assembly —
models corrupt URLs, so the audio link is captured from the render tool's output
and the strudel.cc link is built locally, never taken from the model's prose.

``run_strudel`` is exported so the main ``.agi`` agent can delegate via the
``compose_strudel`` tool.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from agents import Agent, FunctionTool, RunContextWrapper

from cloudbot import hook
from cloudbot.agent.common import parse_args, run_in_executor
from cloudbot.agent.runs import recent_runs, record_run
from cloudbot.agent.subagent import SubagentError, run_subagent
from cloudbot.util import strudel, web
from cloudbot.util.typing import (
    start_typing_for_command,
    stop_typing_for_command,
)

_URL_RE = re.compile(r"https?://\S+")
_AUDIO_RE = re.compile(r"audio_url:\s*(\S+)")


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


def _build_tools(url: str, key: str, specs: list[dict[str, Any]]) -> list[FunctionTool]:
    """Bridge each tool rendel's MCP server exposes to a FunctionTool. Generic, so
    new rendel tools appear automatically — except strudel_render, whose exact
    audio URL is captured from the result (the model is never trusted to repeat
    it) into the run context for deterministic reply assembly."""
    tools: list[FunctionTool] = []
    for spec in specs:
        name = spec.get("name")
        if not name:
            continue

        async def on_invoke(
            ctx: RunContextWrapper, args_json: str, _name: str = name
        ) -> str:
            args = parse_args(args_json)
            text, is_error = await run_in_executor(
                strudel.call_tool, url, key, _name, args
            )
            if (
                _name == "strudel_render"
                and not is_error
                and isinstance(ctx.context, dict)
            ):
                ctx.context["code"] = args.get("code")
                match = _AUDIO_RE.search(text)
                if match:
                    ctx.context["audio_url"] = match.group(1)
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


class _AgentState:
    agent: Agent | None = None
    api: tuple[str, str] | None = None


async def _get_agent(url: str, key: str) -> Agent:
    """Build (once) the StrudelComposer agent: instructions from rendel's
    compose_strudel prompt, tools bridged from rendel's MCP. Neither the prompt
    nor the tool definitions are duplicated here — rendel owns them."""
    if _AgentState.agent is not None and _AgentState.api == (url, key):
        return _AgentState.agent
    instructions = await run_in_executor(
        strudel.get_prompt_text, url, key, "compose_strudel"
    )
    if not instructions:
        raise strudel.StrudelError("rendel compose_strudel prompt is empty")
    specs = await run_in_executor(strudel.list_tools, url, key)
    agent = Agent(
        name="StrudelComposer",
        instructions=instructions,
        tools=_build_tools(url, key, specs),
    )
    _AgentState.agent = agent
    _AgentState.api = (url, key)
    return agent


def _run_limits(bot: Any) -> tuple[int, float]:
    # The guide is one-shot (compose once, render once), so a healthy run is ~2-3
    # turns. Cap low so a looping model can't burn minutes — render is ~8s, the
    # slow part is repeated LLM turns.
    cfg = (bot.config.get("plugins") or {}).get("strudel_agent") or {}
    return int(cfg.get("max_turns", 10)), float(cfg.get("timeout_s", 240))


def _build_reply(text: str, captured: dict[str, str]) -> str:
    """Assemble the chat reply from the model's prose plus the exact URLs the
    tools produced. Any link the model wrote itself is stripped — models retype
    URLs and corrupt them, so links come only from the recorded tool output."""
    audio = captured.get("audio_url")
    share = captured.get("share_url")
    if not (audio or share):
        return text or "(no result)"
    desc = _URL_RE.sub("", text or "").strip()
    desc = re.sub(r"[ \t]{2,}", " ", desc)
    lines = [desc] if desc else []
    if audio:
        lines.append(f"audio: {audio}")
    if share:
        lines.append(f"edit: {share}")
    return "\n".join(lines)


def _recent_songs_note(channel: str) -> str:
    """Recent songs made in this channel; the newest one's code inline so a
    follow-up like 'make it faster' edits it instead of composing from scratch."""
    songs = recent_runs(channel, "song", n=3)
    if not songs:
        return ""
    lines = ["Songs you recently made in this channel (newest first):"]
    for index, song in enumerate(songs):
        lines.append(f'- "{song.summary}" — {song.url}')
        if index == 0 and song.detail:
            lines.append(
                "  Its Strudel code (edit THIS when the user asks to change "
                "that song):\n```\n" + song.detail + "\n```"
            )
    return "\n".join(lines) + "\n\n"


async def run_strudel(bot: Any, prompt: str, channel: str = "") -> str:
    """Compose+render a song via the rendel MCP server and return a short result
    with the URL(s). Shared by the ``.strudel`` command and the main agent's
    ``compose_strudel`` tool. Recent songs in ``channel`` are surfaced so a
    follow-up can iterate on them. Raises on misconfiguration."""
    url, key = strudel.config_from_bot(bot)
    agent = await _get_agent(url, key)
    max_turns, timeout_s = _run_limits(bot)
    ts = datetime.now().strftime("%H:%M:%S")
    enriched = f"{_recent_songs_note(channel)}[time: {ts}]\n{prompt}"
    captured: dict[str, str] = {}
    text = await run_subagent(
        bot,
        agent=agent,
        prompt=enriched,
        max_turns=max_turns,
        timeout_s=timeout_s,
        context=captured,
    )
    # The editor link is a short s.h4ks redirect to strudel.cc (the raw link is
    # ~1.5KB and truncates in chat); the raw link is the fallback if paste fails.
    code = captured.get("code")
    if code:
        try:
            captured["share_url"] = await run_in_executor(strudel.share_short_url, code)
        except web.ServiceError:
            captured["share_url"] = strudel.share_url(code)
    reply = _build_reply(text, captured)
    song_url = captured.get("share_url") or captured.get("audio_url")
    if song_url:
        record_run(
            channel,
            "song",
            _URL_RE.sub("", reply).strip(),
            song_url,
            detail=code or "",
        )
    return reply


@hook.command("strudel", autohelp=False, allow_private=False)
async def strudel_command(text, event):
    """<description> - compose and render a short song with Strudel; returns links."""
    if not text:
        event.reply("usage: .strudel <song description or Strudel code>")
        return
    event.reply("Composing with Strudel, this may take a minute...")
    typing_id = id(event)
    target = event.chan or event.nick
    await start_typing_for_command(event.conn, target, typing_id)
    try:
        answer = await run_strudel(event.bot, text, channel=target)
    except strudel.StrudelNotConfigured:
        event.reply("Strudel not configured.")
        return
    except (strudel.StrudelError, SubagentError) as e:
        event.reply(f"Strudel agent failed: {e}")
        return
    finally:
        await stop_typing_for_command(event.conn, target, typing_id)
    event.reply(answer)
