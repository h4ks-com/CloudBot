"""Filesystem skill catalogue for the agent.

A skill is a markdown file with a small frontmatter block (``name``,
``description``, optional ``agents``) and a body of step-by-step instructions.
The catalogue is surfaced to an agent as a short index (one line per skill) in
its system prompt; the agent loads a skill's full body on demand with the
``read_skill`` tool, so a large playbook only enters context when the agent
judges it relevant.

Skills come from two places. Packaged skills live under
``cloudbot/agent/skills/``; for them the first subfolder is the skill's group
(``skills/kaggle/foo.md`` belongs to group ``kaggle`` and is offered to a
subagent of that name). External skills live in one runtime dir: the bot's
persistent ``data/skills`` by default, or ``plugins.agent.skills.dir`` when
configured. An external skill is one ``<name>/SKILL.md`` folder (or a flat
``<name>.md``); the scan reads a single level, so a repository checkout of
markdown cannot flood the catalogue. :func:`install_from_git` places a skill
repository there at runtime, relocating whichever folder actually carries the
SKILL.md to the top level. An external skill carries no group, so the main
agent sees it; its frontmatter ``agents`` list can widen that. The main agent
is offered every skill regardless of group.
"""

import logging
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("cloudbot")

_SKILLS_DIR = Path(__file__).parent / "skills"
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
# Agent Skills spec name rules: lowercase alphanumeric with single hyphens,
# 1-64 chars. Same regex opencode validates with.
_SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NAME_MAX = 64
_DESCRIPTION_MAX = 1024
_CLONE_TIMEOUT_S = 120
_INSTALL_DEPTH = 3
_ASSET_CAP = 32_000
_LIST_CAP = 200
_BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".wav",
    ".mp3",
    ".mp4",
    ".zip",
    ".gz",
    ".tar",
    ".pdf",
    ".sqlite",
    ".db",
}


def _safe_name(name: str) -> str | None:
    """A name that follows the Agent Skills spec naming rules and so is safe
    to use as a folder name inside the runtime dir."""
    if len(name) > _NAME_MAX or not _SKILL_NAME.match(name):
        return None
    return name


# The main agent's scope name: it is offered every skill, whatever the group.
MAIN_AGENT = "agi"


class BotHandle(Protocol):
    """What the catalogue needs from a bot: its config and persistent data
    path. Both CloudBot and the test MockBot satisfy this structurally; the
    members are read-only so implementations can declare narrower types."""

    @property
    def config(self) -> Mapping[str, object]: ...

    @property
    def data_path(self) -> Path: ...


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    group: str
    agents: tuple[str, ...]
    body: str
    source: str = "packaged"
    path: Path | None = None


def _parse(
    path: Path, group: str = "", source: str = "packaged"
) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        logger.warning("skills: %s is not readable utf-8, skipped", path)
        return None
    match = _FRONTMATTER.match(text)
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
    return Skill(
        name=name,
        description=meta.get("description", ""),
        group=group,
        agents=agents,
        body=match.group(2).strip(),
        source=source,
        path=path,
    )


def skills_root(bot: BotHandle | None) -> Path | None:
    """The external skills dir: the configured override, else data/skills."""
    if bot is None:
        return None
    plugins = bot.config.get("plugins")
    if isinstance(plugins, dict):
        agent_cfg = plugins.get("agent")
        if isinstance(agent_cfg, dict):
            skills_cfg = agent_cfg.get("skills")
            if isinstance(skills_cfg, dict):
                configured = skills_cfg.get("dir")
                if isinstance(configured, str) and configured.strip():
                    return Path(configured).expanduser()
    return bot.data_path / "skills"


def _scan_external(root: Path) -> list[Skill]:
    """One skill per immediate subfolder carrying a SKILL.md, plus flat
    top-level .md files. Only one level is read, so a stray checkout full of
    markdown (a cloned repository) cannot flood the catalogue."""
    if not root.is_dir():
        return []
    skills: list[Skill] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        candidate = entry / "SKILL.md" if entry.is_dir() else entry
        if candidate.suffix != ".md" or not candidate.is_file():
            continue
        skill = _parse(candidate, source=str(entry))
        if skill and skill.name:
            skills.append(skill)
    return skills


def _all_skills(bot: BotHandle | None = None) -> list[Skill]:
    # Scanned fresh each call: a handful of small files, and dropping a new
    # skill in should take effect without restarting the bot.
    skills: list[Skill] = []
    if _SKILLS_DIR.is_dir():
        for path in sorted(_SKILLS_DIR.rglob("*.md")):
            rel = path.relative_to(_SKILLS_DIR)
            group = rel.parts[0] if len(rel.parts) > 1 else ""
            skill = _parse(path, group)
            if skill and skill.name:
                skills.append(skill)
    root = skills_root(bot)
    if root is not None:
        skills.extend(_scan_external(root))
    return skills


def _visible(skill: Skill, agent: str) -> bool:
    if agent == MAIN_AGENT:
        return True
    if skill.agents:
        return agent in skill.agents
    return skill.group == agent


def skill_index(agent: str = MAIN_AGENT, bot: BotHandle | None = None) -> str:
    """The skills an agent can use, as a name+description index for its prompt.

    Bodies are deliberately left out: the agent reads one with read_skill only
    when a request matches, so a long playbook never sits in context unused.
    """
    skills = [s for s in _all_skills(bot) if _visible(s, agent)]
    if not skills:
        return ""
    lines = [
        "\n## Skills",
        "Proven playbooks for specific jobs. BEFORE researching, writing code, "
        "or reusing an old notebook, check this list. If one matches the "
        "request, call read_skill(name) FIRST and follow it exactly — it is a "
        "working recipe, so do NOT figure the job out yourself or research an "
        "API a skill already covers.",
        "Skills load progressively; keep them that way. The skill's "
        "instructions name the supporting files they need (schemas, examples, "
        "references) as paths relative to the skill folder: read exactly "
        "those with read_skill(name, path=...), one at a time, and nothing "
        "else. List a folder with path='.' only when the instructions point "
        "at a file you cannot locate — sizes are shown, so skip fat files "
        "the instructions never mention. Never fetch a skill's own files "
        "from the web.",
        "A skill defines its deliverable, and the format is part of the "
        "contract. When its primary path is blocked, take the skill's own "
        "fallback exactly as written — NEVER silently substitute a cheaper "
        "format (markdown or Mermaid instead of its HTML artifact, a paste "
        "instead of a built file). If neither the primary path nor the "
        "fallback is possible, deliver nothing and say exactly what is "
        "missing.",
    ]
    lines += [f"- {s.name}: {s.description}" for s in skills]
    return "\n".join(lines)


def read_skill_body(name: str, bot: BotHandle | None = None) -> str | None:
    """The full instructions for a skill by name, or None if there is no such
    skill. Lookup is by name across the whole catalogue: the index already
    decides what each agent is told about, so fetching a known name is safe."""
    for skill in _all_skills(bot):
        if skill.name == name:
            return skill.body
    return None


def skill_folder(skill: Skill) -> Path | None:
    """The folder a skill's assets live in. A folder-shaped skill (an
    installed repository) owns its entry dir; a flat markdown skill has none,
    and a packaged skill's folder is its group dir under the packaged root."""
    if skill.path is None:
        return None
    if skill.source == "packaged":
        return skill.path.parent
    entry = Path(skill.source)
    return entry if entry.is_dir() else None


def _describe_entry(entry: Path, base: Path) -> str | None:
    """One listing line with its size, or None when it vanished or broke
    between the walk and the stat."""
    rel = entry.relative_to(base).as_posix()
    try:
        size = entry.stat().st_size
    except OSError:
        return None
    return (
        f"{rel} ({size / 1024:.1f} KB)" if size >= 1024 else f"{rel} ({size} B)"
    )


def read_skill_asset(name: str, rel: str, bot: BotHandle | None) -> str:
    """One file from inside a skill's own folder, or a directory listing.

    This is how a skill's supporting files (schemas, examples, references)
    reach the agent: contained to the skill's folder, capped in size, with
    hidden paths (a leftover .git) unreadable.
    """
    skill = next((s for s in _all_skills(bot) if s.name == name), None)
    if skill is None:
        return f"(error: no skill named '{name}')"
    folder = skill_folder(skill)
    if folder is None:
        return f"(error: skill '{name}' has no folder to read from)"
    rel = rel.strip().strip("/").replace("\\", "/") or "."
    if any(part.startswith(".") for part in Path(rel).parts):
        return "(error: hidden paths are not readable)"
    base = folder.resolve()
    target = (folder / rel).resolve()
    if target != base and base not in target.parents:
        return "(error: path escapes the skill folder)"
    if not target.exists():
        return f"(error: no file at {rel})"
    if target.is_dir():
        # os.walk with followlinks=False: a symlinked directory in a cloned
        # skill cannot recurse, and symlinks are skipped outright so a
        # dangling one cannot crash the size stat.
        entries: list[str] = []
        for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
            dirnames[:] = sorted(
                dirname
                for dirname in dirnames
                if not dirname.startswith(".")
                and not (Path(dirpath) / dirname).is_symlink()
            )
            entries.extend(
                (Path(dirpath) / dirname).relative_to(base).as_posix() + "/"
                for dirname in dirnames
            )
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                entry = Path(dirpath) / filename
                if entry.is_symlink():
                    continue
                described = _describe_entry(entry, base)
                if described:
                    entries.append(described)
        if not entries:
            return "(empty directory)"
        entries.sort()
        listing = "\n".join(entries[:_LIST_CAP])
        if len(entries) > _LIST_CAP:
            listing += f"\n… and {len(entries) - _LIST_CAP} more"
        return (
            f"files under {rel} (sizes shown — read only what the skill's "
            f"instructions reference):\n{listing}"
        )
    if target.suffix.lower() in _BINARY_EXT:
        return f"(error: {rel} is a binary file)"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(error reading {rel}: {e})"
    if len(text) > _ASSET_CAP:
        text = text[:_ASSET_CAP] + f"\n… (truncated at {_ASSET_CAP} chars)"
    return text


def skills_status(bot: BotHandle | None = None) -> list[str]:
    """The whole catalogue with where each skill came from, for .reloadskills."""
    skills = _all_skills(bot)
    if not skills:
        return ["No skills loaded."]
    external = [s for s in skills if s.source != "packaged"]
    packaged = [s.name for s in skills if s.source == "packaged"]
    lines = [f"Skills: {len(packaged)} packaged, {len(external)} external"]
    lines.extend(f"- {s.name}: {s.source}" for s in external)
    lines.append("packaged: " + (", ".join(packaged) or "none"))
    return lines


def _find_skill_md(clone: Path) -> Path | None:
    for depth in range(_INSTALL_DEPTH + 1):
        for candidate in clone.glob("*/" * depth + "SKILL.md"):
            if candidate.is_file():
                return candidate
    return None


def install_from_git(url: str, ref: str, bot: BotHandle) -> str:
    """Clone a skill repository and place its skill folder in the runtime dir.

    Repositories bury the SKILL.md at varying depths (archify keeps it under
    ``archify/``), so the folder that actually carries it moves to the top
    level under the frontmatter's name. Installing an existing name replaces
    it. Returns a chat-ready status line.
    """
    root = skills_root(bot)
    if root is None:
        return "no skills dir available"
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{uuid.uuid4().hex}"
    args = ["git", "clone", "--depth", "1"]
    if ref:
        args += ["--branch", ref]
    args += ["--", url, str(staging)]
    try:
        done = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_CLONE_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return "git is not installed on the bot host"
    except subprocess.TimeoutExpired:
        shutil.rmtree(staging, ignore_errors=True)
        return f"clone timed out after {_CLONE_TIMEOUT_S}s"
    if done.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        return f"clone failed: {(done.stderr or '').strip()[:200]}"
    try:
        skill_md = _find_skill_md(staging)
        if skill_md is None:
            return f"no SKILL.md within {_INSTALL_DEPTH} levels in {url}"
        parsed = _parse(skill_md, source=str(skill_md.parent))
        if parsed is None:
            return f"{skill_md} has no readable frontmatter block"
        # The frontmatter name is remote-controlled text; it must survive as
        # a folder name inside the runtime dir or the install lands elsewhere.
        name = _safe_name(parsed.name) or _safe_name(skill_md.parent.name)
        if not name:
            return f"skill name '{parsed.name}' is not a safe folder name"
        dest = root / name
        if dest.is_dir():
            shutil.rmtree(dest)
        shutil.move(str(skill_md.parent), str(dest))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return (
        f"Installed skill '{name}' ({ref or 'default branch'}) → {dest}"
        + _frontmatter_notes(parsed)
    )


def _frontmatter_notes(parsed: Skill) -> str:
    if not parsed.description:
        return " (warning: no description in frontmatter)"
    if len(parsed.description) > _DESCRIPTION_MAX:
        return (
            f" (warning: description is {len(parsed.description)} chars, "
            f"spec caps it at {_DESCRIPTION_MAX})"
        )
    return ""


def remove_skill(name: str, bot: BotHandle) -> str:
    """Delete an external skill folder by name."""
    root = skills_root(bot)
    if root is None:
        return "no skills dir available"
    if not _safe_name(name):
        return f"'{name}' is not a skill name"
    dest = root / name
    if not dest.is_dir():
        return f"no external skill named '{name}'"
    shutil.rmtree(dest)
    return f"Removed skill '{name}'"
