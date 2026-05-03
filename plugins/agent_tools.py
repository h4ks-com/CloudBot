"""Back-compat shim. Tool definitions live in cloudbot.agent.

Kept so any existing imports `from plugins.agent_tools import CUSTOM_TOOLS,
upload_markdown_paste` keep working (e2e test scripts in tmp/, etc.).
Delete after callers migrate.
"""

from cloudbot.agent import build_custom_tools, upload_markdown_paste

CUSTOM_TOOLS = build_custom_tools()

__all__ = ["CUSTOM_TOOLS", "upload_markdown_paste"]
