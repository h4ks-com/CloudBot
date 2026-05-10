"""Tool modules. Importing this package registers every @tool with the registry."""

from cloudbot.agent.tools import (  # noqa: F401  (side effect: tool registration)
    browser,
    github,
    history,
    memory,
    vibegame,
    vision,
    web,
    webxdc,
    wiki,
)
