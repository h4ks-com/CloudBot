"""Public surface of the agent package consumed by plugins/agent.py.

Importing this package eagerly triggers tool registration via
`cloudbot.agent.tools` (each module's @tool decorators populate the registry
at import time).
"""

# Eager import so all @tool decorators run and the registry is populated
# before any caller invokes build_custom_tools().
import cloudbot.agent.tools  # noqa: F401, E402
from cloudbot.agent.common import (
    fetch_github_username,
    fetch_self_repo_push,
    memory_namespace,
    memory_read_namespaces,
    parse_args,
    parse_scope,
    resolve_config_path,
    safe_tool,
    sanitise_err_message,
    split_repo,
)
from cloudbot.agent.instructions import AGENT_INSTRUCTIONS
from cloudbot.agent.registry import build_custom_tools
from cloudbot.agent.tools.web import upload_markdown_paste

__all__ = [
    "AGENT_INSTRUCTIONS",
    "build_custom_tools",
    "fetch_github_username",
    "fetch_self_repo_push",
    "parse_args",
    "memory_namespace",
    "memory_read_namespaces",
    "parse_scope",
    "resolve_config_path",
    "safe_tool",
    "sanitise_err_message",
    "split_repo",
    "upload_markdown_paste",
]
