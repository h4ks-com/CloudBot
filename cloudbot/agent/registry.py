"""Tool registry + decorator that collapses FunctionTool boilerplate.

Each `tools/*.py` module imports `tool` and decorates async functions. The
decorator wraps them as `FunctionTool` instances and appends to `_REGISTRY`.
`build_custom_tools(bot)` returns the list — called once at agent build time
in `plugins/agent.py`.
"""

import logging
from typing import Any, Awaitable, Callable

from agents import FunctionTool, RunContextWrapper

from cloudbot.agent.common import parse_args, safe_tool

logger = logging.getLogger("cloudbot")

_REGISTRY: list[FunctionTool] = []
_GITHUB_TOOL_NAMES: set[str] = set()


ToolBody = Callable[[RunContextWrapper, dict[str, Any]], Awaitable[str]]


def tool(
    *,
    name: str,
    description: str,
    schema: dict[str, Any],
    wrap_errors: bool = False,
    is_github: bool = False,
) -> Callable[[ToolBody], ToolBody]:
    """Decorate an async function as a FunctionTool.

    Body signature: `async def fn(ctx, data) -> str`.
    The decorator parses args_json into a dict before calling the body, applies
    `safe_tool` if `wrap_errors=True` (every github tool needs it because the
    MCP transport raises on malformed responses), and registers the FunctionTool.
    """

    def decorator(body: ToolBody) -> ToolBody:
        async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
            data = parse_args(args_json)
            return await body(ctx, data)

        invoke = safe_tool(on_invoke) if wrap_errors else on_invoke
        ft = FunctionTool(
            name=name,
            description=description,
            params_json_schema=schema,
            on_invoke_tool=invoke,
        )
        _REGISTRY.append(ft)
        if is_github:
            _GITHUB_TOOL_NAMES.add(name)
        return body

    return decorator


def build_custom_tools() -> list[FunctionTool]:
    """Return the global tool list. The package __init__ already imports
    every tools/* module at load time, populating _REGISTRY via @tool."""
    return list(_REGISTRY)


def github_tool_names() -> set[str]:
    """Names of tools backed by GitHub MCP — used by callers that count budgets."""
    return set(_GITHUB_TOOL_NAMES)
