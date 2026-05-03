"""Tools that surface IRC channel history and bot-command introspection to the agent."""

from datetime import datetime

from cloudbot.agent.registry import tool


@tool(
    name="chat_history",
    description="Get recent chat messages from the current IRC channel. Use to understand conversation context before answering.",
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
    event = ctx.context

    try:
        history = list(event.conn.history[event.chan])
    except (KeyError, AttributeError):
        return "(no history available)"

    recent = history[-n:]
    lines = []
    for nick, timestamp, msg in recent:
        msg = msg.replace("\x01ACTION ", "* ").replace("\x01", "")
        ts = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
        lines.append(f"[{ts}] <{nick}> {msg}")

    return "\n".join(lines) if lines else "(no messages in history)"


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

    event = ctx.context
    try:
        history = list(event.conn.history[event.chan])
    except (KeyError, AttributeError):
        return "(no history available)"

    matches = []
    for nick, timestamp, msg in history:
        if query in msg.lower():
            msg = msg.replace("\x01ACTION ", "* ").replace("\x01", "")
            ts = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
            matches.append(f"[{ts}] <{nick}> {msg}")

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
