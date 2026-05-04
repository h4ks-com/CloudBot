"""GitHub MCP-backed tools: explore, fork, branch, edit, and open PRs.

Every tool in this module hits the GitHub MCP server (or the GitHub REST API
indirectly via fork polling) and is therefore registered with
`is_github=True` so callers can count usage against per-run budgets and so
boundary errors are caught by `safe_tool` rather than aborting the agent run.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from cloudbot.agent.common import fetch_github_username, split_repo
from cloudbot.agent.github_client import (
    STALE_SHA_PATTERN,
    bump_budget,
    extract_file_sha,
    extract_mcp_content,
    mcp_call,
    mcp_call_raw,
    wait_for_fork,
)
from cloudbot.agent.registry import tool

logger = logging.getLogger("cloudbot")


@tool(
    name="list_repo_files",
    description=(
        "List files and directories in a GitHub repo at a given path. "
        "repo format: 'owner/repo'. path defaults to repo root. "
        "Use to explore the codebase structure before reading or editing files."
    ),
    schema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "GitHub repo in 'owner/repo' format",
            },
            "path": {
                "type": "string",
                "description": "Directory path (default: root)",
            },
            "branch": {
                "type": "string",
                "description": "Branch name (default: main)",
            },
        },
        "required": ["repo"],
    },
    wrap_errors=True,
    is_github=True,
)
async def list_repo_files(ctx, data):
    over = bump_budget(ctx.context, "explore")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    path = str(data.get("path") or "").strip()
    branch = str(data.get("branch") or "main").strip()
    if not repo:
        return "(error: repo required, e.g. 'owner/repo')"
    owner, name = split_repo(repo)
    raw = await mcp_call(
        ctx.context,
        "get_file_contents",
        {"owner": owner, "repo": name, "path": path, "ref": branch},
    )
    # Compress dir listings: GitHub returns a JSON array of file objects,
    # each ~200 chars. For large dirs (plugins/ has 200+ files) this
    # blows context. Reduce to "name (type)" lines, capped at 80 entries.
    try:
        entries = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw[:3000]
    if not isinstance(entries, list):
        return raw[:3000]
    lines = [
        f"{e.get('name')} ({e.get('type')})"
        for e in entries[:80]
        if isinstance(e, dict)
    ]
    more = (
        f"\n… +{len(entries) - 80} more entries (filter via path)"
        if len(entries) > 80
        else ""
    )
    return "\n".join(lines) + more


_READ_CAP = 12000


@tool(
    name="read_github_file",
    description=(
        "Read the contents of a file from any GitHub repo. "
        "repo format: 'owner/repo'. Returns raw file text up to 12000 chars, "
        "with a 'SHA: <hash>' header line — the SHA is the file's blob SHA on that branch. "
        "If the file is bigger than 12000 chars, the response includes a TRUNCATED marker AND "
        "the total char count. To read the rest, call again with offset=<chars-read-so-far>. "
        "Pass start_line=N to get a 150-line window around line N (good for jumping to a "
        "specific symbol when you have a #L hint from ghsource). "
        "RULE: do NOT call edit_github_file on a file you have only seen partially. "
        "Either read it fully (paginate with offset) or use a targeted patch tool instead."
    ),
    schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "repo": {
                "type": "string",
                "description": "owner/repo, e.g. 'h4ks-com/CloudBot'",
            },
            "path": {
                "type": "string",
                "description": "File path, e.g. 'plugins/weather.py'",
            },
            "branch": {
                "type": "string",
                "description": "Branch (default: main)",
            },
            "start_line": {
                "type": "integer",
                "description": "Line number from ghsource #L (returns ±100 lines)",
            },
            "offset": {
                "type": "integer",
                "description": "Char offset for paginated reads of a truncated file (default: 0)",
            },
        },
        "required": ["repo", "path"],
    },
    wrap_errors=True,
    is_github=True,
)
async def read_github_file(ctx, data):
    over = bump_budget(ctx.context, "explore")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    path = str(data.get("path") or "").strip()
    branch = str(data.get("branch") or "main").strip()
    start_line = data.get("start_line")
    offset = data.get("offset")
    if not repo or not path:
        return "(error: repo and path required)"
    owner, name = split_repo(repo)
    raw_result = await mcp_call_raw(
        ctx.context,
        "get_file_contents",
        {"owner": owner, "repo": name, "path": path, "ref": branch},
    )
    if isinstance(raw_result, str):
        return raw_result
    body = extract_mcp_content(raw_result)
    sha = extract_file_sha(raw_result)
    sha_line = f"SHA: {sha}\n" if sha else ""
    if start_line is not None:
        try:
            center = int(start_line)
            lines = body.splitlines()
            lo = max(0, center - 30)
            hi = min(len(lines), center + 120)
            excerpt = "\n".join(
                f"{lo+i+1}: {l}" for i, l in enumerate(lines[lo:hi])
            )
            return f"{sha_line}(lines {lo+1}-{hi} of {len(lines)})\n{excerpt}"
        except (ValueError, AttributeError):
            pass
    try:
        off = max(0, int(offset)) if offset is not None else 0
    except (ValueError, TypeError):
        off = 0
    total = len(body)
    chunk = body[off : off + _READ_CAP]
    end = off + len(chunk)
    if total > _READ_CAP or off > 0:
        more_hint = (
            f"(call read_github_file again with offset={end} to continue)"
            if end < total
            else "(end of file reached)"
        )
        return (
            f"{sha_line}TRUNCATED chars {off}-{end} of {total} total. "
            f"{more_hint}\n!!! DO NOT EDIT THIS FILE WITH edit_github_file UNTIL "
            f"YOU HAVE READ ALL {total} CHARS — partial edits silently delete code.\n"
            f"{chunk}"
        )
    return f"{sha_line}{chunk}"


@tool(
    name="search_github_code",
    description=(
        "Search code across GitHub using GitHub code search. "
        "Optionally scope to a specific repo ('owner/repo'). "
        "Use to find where a function or pattern is defined before editing."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (GitHub code search syntax)",
            },
            "repo": {
                "type": "string",
                "description": "Limit to repo 'owner/repo' (optional)",
            },
        },
        "required": ["query"],
    },
    wrap_errors=True,
    is_github=True,
)
async def search_github_code(ctx, data):
    over = bump_budget(ctx.context, "explore")
    if over:
        return over
    query = str(data.get("query") or "").strip()
    repo = str(data.get("repo") or "").strip()
    if not query:
        return "(error: query required)"
    args: dict[str, Any] = {"query": query}
    if repo:
        args["query"] = f"{query} repo:{repo}"
    return await mcp_call(ctx.context, "search_code", args)


@tool(
    name="fork_github_repo",
    description=(
        "Fork a GitHub repo to the authenticated account. "
        "IMPORTANT: fork creation is async — wait ~10 seconds before using the fork. "
        "Use this before editing if you don't have direct write access to the repo."
    ),
    schema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repo to fork in 'owner/repo' format",
            },
        },
        "required": ["repo"],
    },
    wrap_errors=True,
    is_github=True,
)
async def fork_github_repo(ctx, data):
    over = bump_budget(ctx.context, "fork")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    if not repo:
        return "(error: repo required)"
    owner, repo_name = split_repo(repo)
    result = await mcp_call(
        ctx.context,
        "fork_repository",
        {"owner": owner, "repo": repo_name},
    )
    # GitHub returns 202 "Fork is in progress" for first-time forks.
    # Poll the fork's default branch until it exists (max ~30s) so the
    # next branch/edit calls don't 404. If the fork already exists this
    # returns immediately on the first poll.
    bot = ctx.context.bot
    token = bot.config.get_api_key("github") or ""
    if token:
        fork_owner = fetch_github_username(bot)
        if fork_owner:
            ready = await wait_for_fork(
                token, fork_owner, repo_name, max_attempts=10
            )
            if ready:
                return f"{result}\n\n(fork verified ready at {fork_owner}/{repo_name})"
            return (
                f"{result}\n\n(warning: fork at {fork_owner}/{repo_name} not yet "
                f"propagated after 30s — branch creation may 404; retry the workflow once if so)"
            )
    return result


@tool(
    name="create_github_branch",
    description=(
        "Create a new git branch in a GitHub repo. "
        "Use a descriptive name like 'fix/command-name' or 'add/feature-name'. "
        "Always create a branch before editing files."
    ),
    schema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repo in 'owner/repo' format",
            },
            "branch": {"type": "string", "description": "New branch name"},
            "base": {
                "type": "string",
                "description": "Base branch to create from (default: main)",
            },
        },
        "required": ["repo", "branch"],
    },
    wrap_errors=True,
    is_github=True,
)
async def create_github_branch(ctx, data):
    over = bump_budget(ctx.context, "branch")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    branch = str(data.get("branch") or "").strip()
    base = str(data.get("base") or "main").strip()
    if not repo or not branch:
        return "(error: repo and branch required)"
    owner, name = split_repo(repo)
    return await mcp_call(
        ctx.context,
        "create_branch",
        {"owner": owner, "repo": name, "branch": branch, "from_branch": base},
    )


_SHRINK_THRESHOLD = 0.6


@tool(
    name="edit_github_file",
    description=(
        "Create or overwrite a file in a GitHub repo on the given branch. "
        "Provide the COMPLETE new file content — this is a full replacement, not a patch. "
        "Auto-fetches blob SHA, so you do NOT need to supply it (optional sha override). "
        "ALWAYS read_github_file FULLY first (paginate with offset if truncated) before editing. "
        "Always create_github_branch before editing. "
        "SAFETY: if the new content is <60% of the current file size, this tool refuses "
        "the commit (likely a truncated read caused you to forget code). To override the "
        "guard for an intentional shrink (e.g. file deletion / rewrite), pass force_shrink=true."
    ),
    schema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repo in 'owner/repo' format",
            },
            "path": {
                "type": "string",
                "description": "File path, e.g. 'plugins/foo.py'",
            },
            "content": {
                "type": "string",
                "description": "Complete new file content",
            },
            "message": {"type": "string", "description": "Commit message"},
            "branch": {"type": "string", "description": "Branch to commit to"},
            "sha": {
                "type": "string",
                "description": "Optional blob SHA (auto-fetched if omitted)",
            },
            "force_shrink": {
                "type": "boolean",
                "description": "Bypass shrink guard (default false). Only set true for intentional rewrites.",
            },
        },
        "required": ["repo", "path", "content", "branch"],
    },
    wrap_errors=True,
    is_github=True,
)
async def edit_github_file(ctx, data):
    over = bump_budget(ctx.context, "edit")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    path = str(data.get("path") or "").strip()
    content = str(data.get("content") or "")
    message = str(data.get("message") or "Update file via CloudBot AGI").strip()
    branch = str(data.get("branch") or "main").strip()
    sha = str(data.get("sha") or "").strip()
    force_shrink = bool(data.get("force_shrink"))
    if not repo or not path or not content:
        return "(error: repo, path, and content required)"
    owner, repo_name = split_repo(repo)

    async def fetch_probe() -> tuple[str, int]:
        probe = await mcp_call_raw(
            ctx.context,
            "get_file_contents",
            {"owner": owner, "repo": repo_name, "path": path, "ref": branch},
        )
        if not isinstance(probe, dict):
            return "", 0
        existing_sha = extract_file_sha(probe) or ""
        existing_size = len(extract_mcp_content(probe))
        return existing_sha, existing_size

    async def fetch_sha() -> str:
        probe_sha, _ = await fetch_probe()
        return probe_sha

    if not sha:
        sha, existing_size = await fetch_probe()
    else:
        _, existing_size = await fetch_probe()

    if (
        existing_size > 200
        and len(content) < existing_size * _SHRINK_THRESHOLD
        and not force_shrink
    ):
        pct = int(100 * len(content) / existing_size) if existing_size else 0
        return (
            f"(error: shrink guard tripped — new content is {len(content)} chars, "
            f"existing file is {existing_size} chars ({pct}%). This usually means "
            f"you read a truncated view of the file and rewrote from memory, dropping "
            f"code. Read the FULL file first (use offset to paginate past truncation), "
            f"then call edit_github_file again. If this shrink is intentional, "
            f"pass force_shrink=true.)"
        )

    result = "(error: edit_github_file produced no result)"
    for _ in range(3):
        args = {
            "owner": owner,
            "repo": repo_name,
            "path": path,
            "content": content,
            "message": message,
            "branch": branch,
        }
        if sha:
            args["sha"] = sha
        result = await mcp_call(ctx.context, "create_or_update_file", args)
        # Recover from parallel-commit / stale-SHA races by parsing the
        # MCP error which carries the current SHA, then retry.
        if "SHA mismatch" in result or "is stale" in result:
            m = STALE_SHA_PATTERN.search(result)
            if m:
                sha = m.group(1)
                logger.info(
                    "edit_github_file: stale SHA, retrying with %s", sha[:12]
                )
                continue
            sha = await fetch_sha()
            continue
        # File-already-exists arrives when caller passed no SHA AND probe
        # missed (e.g. branch was created moments ago and the cache hadn't
        # propagated yet). Fetch fresh and retry.
        if "File already exists" in result and "must provide" in result:
            sha = await fetch_sha()
            if not sha:
                return result
            continue
        return result
    return result


@tool(
    name="open_github_pr",
    description=(
        "Open a GitHub pull request from head branch into base branch. "
        "For cross-fork PRs use 'forkowner:branchname' as head. "
        "Set draft=true for work-in-progress PRs. "
        "Returns the PR URL — report it to the user."
    ),
    schema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Target repo in 'owner/repo' format",
            },
            "title": {"type": "string", "description": "PR title"},
            "body": {"type": "string", "description": "PR description"},
            "head": {
                "type": "string",
                "description": "Source branch (or 'forkowner:branch')",
            },
            "base": {
                "type": "string",
                "description": "Target branch (default: main)",
            },
            "draft": {
                "type": "boolean",
                "description": "Open as draft PR (default: false)",
            },
        },
        "required": ["repo", "title", "head"],
    },
    wrap_errors=True,
    is_github=True,
)
async def open_github_pr(ctx, data):
    repo = str(data.get("repo") or "").strip()
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "Automated PR by CloudBot AGI").strip()
    head = str(data.get("head") or "").strip()
    base = str(data.get("base") or "main").strip()
    draft = bool(data.get("draft", False))
    if not repo or not title or not head:
        return "(error: repo, title, and head required)"
    owner, name = split_repo(repo)
    return await mcp_call(
        ctx.context,
        "create_pull_request",
        {
            "owner": owner,
            "repo": name,
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": draft,
        },
    )
