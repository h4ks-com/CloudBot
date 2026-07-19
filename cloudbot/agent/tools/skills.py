"""read_skill — load a named skill's full instructions on demand."""

from cloudbot.agent.registry import tool
from cloudbot.agent.skills import read_skill_body


@tool(
    name="read_skill",
    description=(
        "Load the full instructions for a named skill — a step-by-step playbook "
        "for a specific job. When the Skills index in your prompt lists one that "
        "fits the request, call this FIRST to get its exact steps, tools, and "
        "fixed names, then follow them. Do not attempt the job from the name alone."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name exactly as shown in the Skills index.",
            }
        },
        "required": ["name"],
    },
)
async def read_skill(ctx, data) -> str:
    name = str(data.get("name", "")).strip()
    if not name:
        return "(error: name required)"
    body = read_skill_body(name)
    if body is None:
        return f"(error: no skill named '{name}')"
    return body
