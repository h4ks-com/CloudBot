"""Tool modules. Importing this package registers every @tool with the registry."""

from cloudbot.agent.tools import (  # noqa: F401  (side effect: tool registration)
    audio,
    browser,
    context7,
    github,
    history,
    hyperframes,
    kaggle,
    memory,
    sketchfab,
    skills,
    strudel,
    suno,
    vibegame,
    vision,
    wait,
    web,
    wiki,
)
