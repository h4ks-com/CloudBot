"""Reusable runner for specialized sub-agents.

A sub-agent is a single-purpose ``agents.Agent`` (its own instructions + a
small tool set) that runs on the same LLM backend as the main ``.agi`` agent.
This module encapsulates the shared plumbing — backend/fallback selection from
``plugins.agent`` config, ``RunConfig`` construction, and the bounded
``Runner.run`` under a timeout — so each new sub-agent plugin only has to
define its Agent and call :func:`run_subagent`.

Lives outside ``plugins/`` so the plugin manager never loads it as a plugin.
``_make_run_config`` is imported from ``plugins.agent`` (the single source of
truth for backend wiring); it has no @hook side effects, so the import is safe.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents import Agent, RunConfig, Runner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from cloudbot.agent.common import resolve_config_path
from plugins.agent import _make_run_config

logger = logging.getLogger("cloudbot")


class SubagentError(Exception):
    """The sub-agent could not run on any configured backend."""


def agent_config(bot: Any) -> dict[str, Any]:
    """The shared ``plugins.agent`` config block (backends, model, etc.)."""
    return (bot.config.get("plugins") or {}).get("agent") or {}


def _backends_to_try(agent_cfg: dict[str, Any]) -> list[str]:
    backend = agent_cfg.get("backend", "z_ai")
    fallback = agent_cfg.get("fallback_backend")
    order = [backend]
    if fallback and fallback != backend:
        order.append(fallback)
    return order


def _thinking_off_config(
    agent_cfg: dict[str, Any], bot: Any, backend: str, model: str
) -> RunConfig:
    """RunConfig whose client sends ``thinking={"type": "disabled"}``. The GLM models
    reason on every call by default, which burns the agent's per-turn budget for no
    gain on routine tool-driven work. Bearer-auth (OpenAI-compatible) backends only."""
    b = agent_cfg["backends"][backend]
    api_key = resolve_config_path(bot, b.get("api_key_config_path", ""))
    if not api_key:
        raise ValueError(f"agent backend '{backend}' missing api key")
    client = AsyncOpenAI(base_url=b["base_url"], api_key=api_key)
    original = client.chat.completions.create

    async def _create(*args: Any, **kwargs: Any) -> Any:
        extra = dict(kwargs.get("extra_body") or {})
        extra["thinking"] = {"type": "disabled"}
        kwargs["extra_body"] = extra
        return await original(*args, **kwargs)

    client.chat.completions.create = _create
    return RunConfig(model=OpenAIChatCompletionsModel(model=model, openai_client=client))


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
) -> str:
    """Run ``agent`` on ``prompt`` and return its final text output.

    Tries the configured backend then the fallback (same precedence as the
    main agent and the sandbox agents), each under a hard ``timeout_s``. Raises
    :class:`SubagentError` if the agent is disabled/unconfigured or every
    backend fails. ``context`` is passed to the run so tools can record results
    the caller reads back (e.g. exact URLs, instead of trusting the model to
    retype them).
    """
    agent_cfg = agent_config(bot)
    if not agent_cfg.get("enabled", False) or not agent_cfg.get("backends"):
        raise SubagentError("agent not configured")

    run_context = context if context is not None else {}
    last_err: BaseException | None = None
    for backend in _backends_to_try(agent_cfg):
        b = (agent_cfg.get("backends") or {}).get(backend) or {}
        backend_model = model or b.get("model")
        thinking_off = disable_thinking and b.get("auth_header") != "x-api-key"
        try:
            # A sub-agent can pin a different model than .agi and turn off the GLM
            # per-turn reasoning. Both apply only to OpenAIProvider/Bearer backends,
            # which resolve the model by name (every non-ollama backend).
            if thinking_off and backend_model:
                run_cfg = _thinking_off_config(agent_cfg, bot, backend, backend_model)
            else:
                run_cfg = _make_run_config(agent_cfg, bot, backend)
                if model:
                    run_cfg.model = model
        except (ValueError, KeyError) as e:
            logger.warning(
                "subagent: cannot build run config for %s: %s", backend, e
            )
            last_err = e
            continue
        try:
            result = await asyncio.wait_for(
                Runner.run(
                    agent,
                    prompt,
                    context=run_context,
                    run_config=run_cfg,
                    max_turns=max_turns,
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as e:
            logger.warning(
                "subagent: %s timed out after %ss", backend, timeout_s
            )
            last_err = e
            continue
        except (
            Exception
        ) as e:  # noqa: BLE001 — backend failure must fall through to the next
            logger.warning(
                "subagent: %s failed: %s: %s", backend, type(e).__name__, e
            )
            last_err = e
            continue
        return str(result.final_output or "").strip() or "(no answer)"

    err_name = type(last_err).__name__ if last_err else "unknown"
    raise SubagentError(f"all backends failed ({err_name})")
