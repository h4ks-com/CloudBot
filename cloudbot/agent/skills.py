"""Filesystem skill catalogue for the agent.

A skill is a markdown file under ``cloudbot/agent/skills/`` with a small
frontmatter block (``name``, ``description``, optional ``agents``) and a body of
step-by-step instructions. The catalogue is surfaced to an agent as a short index
(one line per skill) in its system prompt; the agent loads a skill's full body on
demand with the ``read_skill`` tool, so a large playbook only enters context when
the agent judges it relevant.

The first subfolder is the skill's group: ``skills/kaggle/foo.md`` belongs to
group ``kaggle`` and is offered to a subagent of that name. The main agent is
offered every skill regardless of group.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent / "skills"
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)

# The main agent's scope name: it is offered every skill, whatever the group.
MAIN_AGENT = "agi"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    group: str
    agents: tuple[str, ...]
    body: str


def _parse(path: Path) -> Skill | None:
    match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        return None
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    name = meta.get("name") or path.stem
    agents = tuple(
        a.strip()
        for a in meta.get("agents", "").strip("[]").split(",")
        if a.strip()
    )
    rel = path.relative_to(_SKILLS_DIR)
    group = rel.parts[0] if len(rel.parts) > 1 else ""
    return Skill(
        name=name,
        description=meta.get("description", ""),
        group=group,
        agents=agents,
        body=match.group(2).strip(),
    )


def _all_skills() -> list[Skill]:
    # Scanned fresh each call: a handful of small files, and dropping a new
    # markdown in should take effect without restarting the bot.
    if not _SKILLS_DIR.is_dir():
        return []
    skills = []
    for path in sorted(_SKILLS_DIR.rglob("*.md")):
        skill = _parse(path)
        if skill and skill.name:
            skills.append(skill)
    return skills


def _visible(skill: Skill, agent: str) -> bool:
    if agent == MAIN_AGENT:
        return True
    if skill.agents:
        return agent in skill.agents
    return skill.group == agent


def skill_index(agent: str = MAIN_AGENT) -> str:
    """The skills an agent can use, as a name+description index for its prompt.

    Bodies are deliberately left out: the agent reads one with read_skill only
    when a request matches, so a long playbook never sits in context unused.
    """
    skills = [s for s in _all_skills() if _visible(s, agent)]
    if not skills:
        return ""
    lines = [
        "\n## Skills",
        "Proven playbooks for specific jobs. BEFORE researching, writing code, "
        "or reusing an old notebook, check this list. If one matches the "
        "request, call read_skill(name) FIRST and follow it exactly — it is a "
        "working recipe, so do NOT figure the job out yourself or research an "
        "API a skill already covers.",
    ]
    lines += [f"- {s.name}: {s.description}" for s in skills]
    return "\n".join(lines)


def read_skill_body(name: str) -> str | None:
    """The full instructions for a skill by name, or None if there is no such
    skill. Lookup is by name across the whole catalogue: the index already
    decides what each agent is told about, so fetching a known name is safe."""
    for skill in _all_skills():
        if skill.name == name:
            return skill.body
    return None
