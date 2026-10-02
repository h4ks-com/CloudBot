"""Tools that surface IRC channel history and bot-command introspection to the agent."""

import time

from cloudbot.agent.common import CONVERSATION_LINES, channel_lines, chat_line
from cloudbot.agent.registry import tool


def _lines_before_request(ctx) -> list[tuple[float, str, str]]:
    """The channel's lines up to the moment the request arrived.

    Lines said while the agent works stay out, the same as in its prompt.
    """
    event = ctx.context
    until = getattr(event, "agent_request_time", None) or time.time()
    return channel_lines(
        getattr(event, "conn", None), getattr(event, "chan", ""), until
    )


@tool(
    name="chat_history",
    description=(
        "Fetch channel lines older than the ones already in your system context. "
        "Use this tool ONLY when you need older history (up to 100 lines back) to "
        "understand a reference or follow a conversation thread."
    ),
    schema={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of recent messages to fetch (default 20, max 100)",
            }
        },
    },
)
async def chat_history(ctx, data):
    n = min(int(data.get("n") or 20), 100)
    older = _lines_before_request(ctx)[:-CONVERSATION_LINES][-n:]
    lines = [chat_line(nick, ts, msg) for ts, nick, msg in older]
    return "\n".join(lines) if lines else "(no older messages in history)"


@tool(
    name="search_history",
    description="Search recent channel messages for a keyword or phrase. Returns matching lines with timestamps.",
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword or phrase to search for (case-insensitive)",
            }
        },
        "required": ["query"],
    },
)
async def search_history(ctx, data):
    query = str(data.get("query") or "").strip().lower()
    if not query:
        return "(error: query required)"

    matches = [
        chat_line(nick, ts, msg)
        for ts, nick, msg in _lines_before_request(ctx)
        if query in msg.lower()
    ]
    if not matches:
        return f"(no messages found containing '{query}')"
    return "\n".join(matches[-30:])


@tool(
    name="list_bot_commands",
    description=(
        "Return a sorted list of all bot command names (without dot prefix). "
        "Use when ghsource says a command is not found and you need to find the correct name — "
        "call this, scan the list for a close match or spelling variant, then call ghsource again."
    ),
    schema={"type": "object", "properties": {}},
)
async def list_bot_commands(ctx, data):
    event = ctx.context
    try:
        cmds = sorted(event.bot.plugin_manager.commands.keys())
    except AttributeError:
        return "(error: command list unavailable)"
    return ", ".join(cmds)
