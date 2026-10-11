"""Which backend the agent runs on, and what that backend brings with it.

Backends are declared in order under ``plugins.agent.backends``; the first is
the default and the rest are fallbacks. An admin can point the agent at any of
them at runtime with ``.agimodel set``. Each backend carries its own optional
escalation model, and declares whether the model reads images itself, so
swapping the backend swaps those capabilities with it.

The override is one setting for the whole process, by design: an admin moves
every connection and channel at once, and a restart returns to the first
backend in config.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("cloudbot")

_override: str | None = None


class Escalation(BaseModel):
    """The stronger model a backend hands the whole conversation to. Every
    field is explicit so any pair works, such as haiku escalating to sonnet."""

    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key_config_path: str = ""

    def is_usable(self) -> bool:
        return bool(
            self.enabled
            and self.base_url
            and self.model
            and self.api_key_config_path
        )


class Backend(BaseModel):
    """One OpenAI-compatible endpoint the agent can run on."""

    name: str
    base_url: str
    model: str
    api_key_config_path: str = ""
    auth_header: str = ""
    multimodal: bool = False
    big_brain: Escalation = Field(default_factory=Escalation)


def agent_config(bot: Any) -> dict[str, Any]:
    """The shared ``plugins.agent`` config block (backends, model, etc.)."""
    return (bot.config.get("plugins") or {}).get("agent") or {}


def all_backends(bot: Any) -> dict[str, Backend]:
    """Every backend config parses, in declaration order. One malformed entry
    is skipped so it cannot take the working ones down with it."""
    parsed: dict[str, Backend] = {}
    for name, raw in (agent_config(bot).get("backends") or {}).items():
        try:
            parsed[name] = Backend(name=name, **(raw or {}))
        except ValidationError as e:
            logger.warning("agent: backend %r is misconfigured: %s", name, e)
    return parsed


def names(bot: Any) -> list[str]:
    return list(all_backends(bot))


def config_for(bot: Any, name: str) -> Backend | None:
    return all_backends(bot).get(name)


def active_name(bot: Any) -> str:
    available = names(bot)
    if _override in available:
        return str(_override)
    return available[0] if available else ""


def active(bot: Any) -> Backend | None:
    return config_for(bot, active_name(bot))


def set_active(bot: Any, name: str) -> bool:
    """Point the agent at ``name``. False when no such backend is configured."""
    global _override
    if name not in names(bot):
        return False
    _override = name
    return True


def order(bot: Any) -> list[str]:
    """The active backend first, then the others as fallbacks in config order."""
    first = active_name(bot)
    if not first:
        return []
    return [first, *[name for name in names(bot) if name != first]]


def reads_images(bot: Any) -> bool:
    """Whether the active model takes images directly, so the agent needs no
    vision tool to look at one."""
    backend = active(bot)
    return bool(backend and backend.multimodal)


def escalation(bot: Any) -> Escalation:
    """The stronger model the active backend escalates to, when it has one."""
    backend = active(bot)
    return backend.big_brain if backend else Escalation()
