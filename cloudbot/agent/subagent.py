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

from agents import Agent, Runner

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


async def run_subagent(
    bot: Any,
    *,
    agent: Agent,
    prompt: str,
    max_turns: int,
    timeout_s: float,
    context: Any = None,
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
        try:
            run_cfg = _make_run_config(agent_cfg, bot, backend)
        except (ValueError, KeyError) as e:
            logger.warning("subagent: cannot build run config for %s: %s", backend, e)
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
            logger.warning("subagent: %s timed out after %ss", backend, timeout_s)
            last_err = e
            continue
        except Exception as e:  # noqa: BLE001 — backend failure must fall through to the next
            logger.warning(
                "subagent: %s failed: %s: %s", backend, type(e).__name__, e
            )
            last_err = e
            continue
        return str(result.final_output or "").strip() or "(no answer)"

    err_name = type(last_err).__name__ if last_err else "unknown"
    raise SubagentError(f"all backends failed ({err_name})")
