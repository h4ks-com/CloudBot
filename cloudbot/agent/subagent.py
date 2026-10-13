"""Reusable runner for specialized sub-agents.

A sub-agent is a single-purpose ``agents.Agent`` (its own instructions + a
small tool set) that runs on the same LLM backend as the main ``.agi`` agent.
This module encapsulates the shared plumbing (picking the active backend from
``plugins.agent`` config, ``RunConfig`` construction, and the bounded
``Runner.run`` under a timeout) so each new sub-agent plugin only has to
define its Agent and call :func:`run_subagent`.

Lives outside ``plugins/`` so the plugin manager never loads it as a plugin.
``_make_run_config`` is imported from ``plugins.agent`` (the single source of
truth for backend wiring); it has no @hook side effects, so the import is safe.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from agents import Agent, RunConfig, RunHooks, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from cloudbot.agent import backends
from cloudbot.agent.common import resolve_config_path
from plugins.agent import _make_run_config

logger = logging.getLogger("cloudbot")


class SubagentError(Exception):
    """The sub-agent could not run on any configured backend."""


agent_config = backends.agent_config


# (sid, step_type, state, tool, content) -> None. Fed by _SubagentProfiler so a
# caller can stream a sub-agent's tool calls as a draft/bot-tools workflow.
ToolStepSink = Callable[[str, str, str, str, object], None]


class _SubagentProfiler(RunHooks):
    def __init__(
        self,
        backend: str,
        agent_name: str,
        on_tool_step: ToolStepSink | None = None,
    ):
        self.backend = backend
        self.agent_name = agent_name
        self.on_tool_step = on_tool_step
        self.t0 = time.monotonic()
        self.last_event = self.t0
        self.tool_count = 0
        # Open tool-call step ids per tool name, so on_tool_end pairs its result
        # with the right start FIFO. The Agents SDK runs a turn's tool calls
        # concurrently, so pairing by call ordinal would cross the wires.
        self._open_sids: dict[str, list[str]] = {}

    def _emit(self, kind: str, name: str, extra: str) -> None:
        now = time.monotonic()
        elapsed = now - self.t0
        since_last = now - self.last_event
        self.last_event = now
        logger.info(
            "[SUBAGENT_PROF] backend=%s agent=%s t+%6.1fs (+%.1fs) %s tool=%s %s",
            self.backend,
            self.agent_name,
            elapsed,
            since_last,
            kind,
            name,
            extra,
        )

    async def on_tool_start(self, ctx, agent, tool):
        self.tool_count += 1
        self._emit("START", tool.name, f"#{self.tool_count}")
        if self.on_tool_step is not None:
            sid = "s" + uuid.uuid4().hex[:10]
            self._open_sids.setdefault(tool.name, []).append(sid)
            self.on_tool_step(sid, "tool-call", "start", tool.name, None)

    async def on_tool_end(self, ctx, agent, tool, result):
        result_str = str(result) if result is not None else ""
        self._emit("END", tool.name, f"result={len(result_str)}B")
        if self.on_tool_step is not None:
            failed = result_str[:20].startswith(
                ("(error", "(mcp", "(tool error")
            )
            state = "failed" if failed else "complete"
            open_sids = self._open_sids.get(tool.name)
            call_sid = (
                open_sids.pop(0) if open_sids else "s" + uuid.uuid4().hex[:10]
            )
            self.on_tool_step(call_sid, "tool-call", state, tool.name, None)
            self.on_tool_step(
                "r" + uuid.uuid4().hex[:10],
                "tool-result",
                state,
                tool.name,
                result_str[:200],
            )


def _bound_model_config(
    agent_cfg: dict[str, Any],
    bot: Any,
    backend: str,
    model: str,
    *,
    thinking_off: bool,
) -> RunConfig:
    """Bind ``model`` to the backend's own OpenAI-compatible client so a sub-agent's
    model override reaches that backend (a bare ``RunConfig.model`` string routes to the
    SDK's default OpenAI endpoint instead). When ``thinking_off`` is set the client also
    sends ``thinking={"type": "disabled"}`` to skip GLM's per-turn reasoning.
    Bearer-auth (OpenAI-compatible) backends only."""
    chosen = backends.config_for(bot, backend)
    if chosen is None:
        raise ValueError(f"agent backend '{backend}' not configured")
    api_key = resolve_config_path(bot, chosen.api_key_config_path)
    if not api_key:
        raise ValueError(f"agent backend '{backend}' missing api key")
    client = AsyncOpenAI(base_url=chosen.base_url, api_key=api_key)
    if thinking_off:
        original = client.chat.completions.create

        async def _create(*args: Any, **kwargs: Any) -> Any:
            extra = dict(kwargs.get("extra_body") or {})
            extra["thinking"] = {"type": "disabled"}
            kwargs["extra_body"] = extra
            return await original(*args, **kwargs)

        client.chat.completions.create = _create  # type: ignore[method-assign]
    return RunConfig(
        model=OpenAIChatCompletionsModel(model=model, openai_client=client)
    )


async def run_subagent(
    bot: Any,
    *,
    agent: Agent,
    prompt: str,
    max_turns: int,
    timeout_s: float,
    context: Any = None,
    model: str | None = None,
    disable_thinking: bool = False,
    on_tool_step: ToolStepSink | None = None,
) -> str:
    """Run ``agent`` on ``prompt`` and return its final text output.

    Runs on the backend the bot is set to, under a hard ``timeout_s``, and
    raises :class:`SubagentError` when the agent is unconfigured or the run
    fails. ``context`` is passed to the run so tools can record results the
    caller reads back (e.g. exact URLs, instead of trusting the model to retype
    them). ``on_tool_step``, when given, is called on every tool start/end so
    the caller can stream the run as a draft/bot-tools workflow.
    """
    agent_cfg = agent_config(bot)
    if not agent_cfg.get("enabled", False) or not agent_cfg.get("backends"):
        raise SubagentError("agent not configured")

    run_context = context if context is not None else {}
    backend = backends.active_name(bot)
    if not backend:
        raise SubagentError("no agent backend configured")

    b = (agent_cfg.get("backends") or {}).get(backend) or {}
    backend_model = model or b.get("model")
    bearer = b.get("auth_header") != "x-api-key"
    thinking_off = disable_thinking and bearer
    try:
        # Bearer backends with an override or thinking-off need a bound client;
        # x-api-key backends (ollama) keep their configured model via _make_run_config.
        if bearer and backend_model and (thinking_off or model):
            run_cfg = _bound_model_config(
                agent_cfg,
                bot,
                backend,
                backend_model,
                thinking_off=thinking_off,
            )
        else:
            run_cfg = _make_run_config(agent_cfg, bot, backend)
    except (ValueError, KeyError) as e:
        raise SubagentError(f"backend {backend} is misconfigured: {e}") from e

    profiler = _SubagentProfiler(backend, agent.name, on_tool_step)
    try:
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                prompt,
                context=run_context,
                run_config=run_cfg,
                max_turns=max_turns,
                hooks=profiler,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        logger.warning(
            "subagent: %s timed out after %ss (%d tool calls)",
            backend,
            timeout_s,
            profiler.tool_count,
        )
        raise SubagentError(
            f"ran out of time after {timeout_s:.0f}s "
            f"({profiler.tool_count} tool calls)"
        ) from e
    except MaxTurnsExceeded as e:
        raise SubagentError(
            f"agent hit the {max_turns}-turn limit before finishing"
        ) from e
    except Exception as e:  # noqa: BLE001 — any backend failure is reported, not retried
        logger.warning(
            "subagent: %s failed: %s: %s", backend, type(e).__name__, e
        )
        # Some exceptions carry no message at all (asyncio.TimeoutError), which
        # renders as a bare "TimeoutError: " that tells the user nothing.
        detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        raise SubagentError(f"{backend} failed ({detail[:160]})") from e
    return str(result.final_output or "").strip() or "(no answer)"
