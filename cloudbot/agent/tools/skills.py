"""read_skill — load a named skill's instructions and assets on demand."""

from cloudbot.agent.registry import tool
from cloudbot.agent.skills import read_skill_asset, read_skill_body


@tool(
    name="read_skill",
    description=(
        "Load a skill's instructions, and optionally its supporting files. "
        "With only a name it returns the skill's full step-by-step playbook — "
        "when the Skills index in your prompt lists one that fits the request, "
        "call this FIRST and follow it exactly. Pass path to read one file "
        "from that skill's own folder (e.g. schemas/workflow.schema.json), "
        "relative to the folder holding its SKILL.md; a directory path "
        "returns its listing, so path='.' lists what the skill ships."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name exactly as shown in the Skills index.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Optional file or directory inside the skill's own "
                    "folder, relative to its SKILL.md. Omit to read the "
                    "skill instructions themselves."
                ),
            },
        },
        "required": ["name"],
    },
)
async def read_skill(ctx, data) -> str:
    name = str(data.get("name", "")).strip()
    if not name:
        return "(error: name required)"
    bot = getattr(ctx.context, "bot", None)
    path = str(data.get("path") or "").strip()
    if path:
        return read_skill_asset(name, path, bot)
    body = read_skill_body(name, bot)
    if body is None:
        return f"(error: no skill named '{name}')"
    return body
