"""Tool modules. Importing this package registers every @tool with the registry."""

from cloudbot.agent.tools import (  # noqa: F401  (side effect: tool registration)
    browser,
    context7,
    github,
    history,
    memory,
    sketchfab,
    vibegame,
    vision,
    web,
    wiki,
)
