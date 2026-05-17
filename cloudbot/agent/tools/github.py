"""GitHub MCP-backed tools: explore, fork, branch, edit, and open PRs.

Every tool in this module hits the GitHub MCP server (or the GitHub REST API
indirectly via fork polling) and is therefore registered with
`is_github=True` so callers can count usage against per-run budgets and so
boundary errors are caught by `safe_tool` rather than aborting the agent run.
"""

from __future__ import annotations

import ast
import json
import logging

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
    args: dict[str, str] = {"query": query}
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


async def _fetch_body_sha(
    ctx, owner: str, repo_name: str, path: str, branch: str
) -> tuple[str | None, str]:
    """Return (body, sha) for a file, or (None, error_string) on failure."""
    raw = await mcp_call_raw(
        ctx.context,
        "get_file_contents",
        {"owner": owner, "repo": repo_name, "path": path, "ref": branch},
    )
    if isinstance(raw, str):
        return None, raw
    if not isinstance(raw, dict):
        return None, "(error: unexpected MCP response)"
    body = extract_mcp_content(raw)
    if body.startswith("(error:"):
        return None, body
    sha = extract_file_sha(raw) or ""
    return body, sha


async def _commit_with_sha_retry(
    ctx,
    owner: str,
    repo_name: str,
    path: str,
    branch: str,
    new_body: str,
    message: str,
    sha: str,
) -> str:
    """create_or_update_file with stale-SHA / parallel-commit retry. Mirrors
    edit_github_file's retry loop so str_replace and insert tools share it."""
    result = "(error: commit produced no result)"
    for _ in range(3):
        args: dict[str, str] = {
            "owner": owner,
            "repo": repo_name,
            "path": path,
            "content": new_body,
            "message": message,
            "branch": branch,
        }
        if sha:
            args["sha"] = sha
        result = await mcp_call(ctx.context, "create_or_update_file", args)
        if "SHA mismatch" in result or "is stale" in result:
            m = STALE_SHA_PATTERN.search(result)
            if m:
                sha = m.group(1)
                continue
            body2, sha2 = await _fetch_body_sha(
                ctx, owner, repo_name, path, branch
            )
            sha = sha2 if body2 is not None else sha
            continue
        if "File already exists" in result and "must provide" in result:
            _, sha = await _fetch_body_sha(ctx, owner, repo_name, path, branch)
            if not sha:
                return result
            continue
        return result
    return result


@tool(
    name="str_replace_github_file",
    description=(
        "Surgical find-and-replace inside a GitHub file. PREFERRED over edit_github_file "
        "for any change that targets specific lines (the most common case). You provide "
        "old_str (must match EXACTLY ONCE in the file, whitespace included) and new_str. "
        "Server reads the file, replaces, commits — you never touch the rest of the file. "
        "Eliminates the 'truncated read drops code' problem entirely. "
        "On no match: 'Error: No match found ...'. On multiple matches: "
        "'Error: Found N matches ...' — add 3-5 lines of surrounding context to old_str "
        "to make it unique. Line endings are normalised to LF on both sides before matching. "
        "Always create_github_branch first; this tool commits to the given branch."
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
            "branch": {"type": "string", "description": "Branch to commit to"},
            "old_str": {
                "type": "string",
                "description": (
                    "Exact substring to find (must match exactly once including "
                    "whitespace). Include 3-5 lines of context if needed for uniqueness."
                ),
            },
            "new_str": {
                "type": "string",
                "description": "Replacement string (may be empty for deletion).",
            },
            "message": {
                "type": "string",
                "description": "Commit message (default: 'Edit via str_replace')",
            },
        },
        "required": ["repo", "path", "branch", "old_str", "new_str"],
    },
    wrap_errors=True,
    is_github=True,
)
async def str_replace_github_file(ctx, data):
    over = bump_budget(ctx.context, "edit")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    path = str(data.get("path") or "").strip()
    branch = str(data.get("branch") or "main").strip()
    old_str = str(data.get("old_str") or "")
    new_str = str(data.get("new_str") or "")
    message = str(data.get("message") or "Edit via str_replace").strip()
    if not repo or not path or not branch or not old_str:
        return "(error: repo, path, branch, and old_str required)"
    if old_str == new_str:
        return "(error: old_str equals new_str — nothing to replace)"
    owner, repo_name = split_repo(repo)

    body, sha_or_err = await _fetch_body_sha(
        ctx, owner, repo_name, path, branch
    )
    if body is None:
        return f"(error: could not read file: {sha_or_err})"
    sha = sha_or_err

    body_lf = body.replace("\r\n", "\n")
    old_lf = old_str.replace("\r\n", "\n")
    new_lf = new_str.replace("\r\n", "\n")
    count = body_lf.count(old_lf)
    if count == 0:
        return (
            "Error: No match found for old_str. Re-read the file (read_github_file) "
            "and try again with text that exists in the current file. Whitespace "
            "must match byte-for-byte."
        )
    if count > 1:
        return (
            f"Error: Found {count} matches for old_str. Add more surrounding "
            f"context (3-5 lines before/after) to make the match unique."
        )
    new_body_lf = body_lf.replace(old_lf, new_lf, 1)
    new_body = (
        new_body_lf.replace("\n", "\r\n")
        if "\r\n" in body and "\r\n" not in body_lf
        else new_body_lf
    )
    result = await _commit_with_sha_retry(
        ctx, owner, repo_name, path, branch, new_body, message, sha
    )
    if result.startswith("(error") or result.startswith("Error"):
        return result
    return f"Successfully replaced text at exactly one location. {result}"


@tool(
    name="insert_at_line_github_file",
    description=(
        "Insert text at a specific line in a GitHub file. after_line=0 prepends to the "
        "top of the file; after_line=N inserts AFTER the Nth line (1-indexed). Useful "
        "for adding imports, list entries, or any append where there's no unique anchor "
        "string for str_replace_github_file. Always create_github_branch first."
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
            "branch": {"type": "string", "description": "Branch to commit to"},
            "after_line": {
                "type": "integer",
                "description": "Line number to insert AFTER (0 = prepend, file_total = append)",
            },
            "text": {
                "type": "string",
                "description": "Text to insert. Trailing newline added if missing.",
            },
            "message": {
                "type": "string",
                "description": "Commit message (default: 'Insert via line tool')",
            },
        },
        "required": ["repo", "path", "branch", "after_line", "text"],
    },
    wrap_errors=True,
    is_github=True,
)
async def insert_at_line_github_file(ctx, data):
    over = bump_budget(ctx.context, "edit")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    path = str(data.get("path") or "").strip()
    branch = str(data.get("branch") or "main").strip()
    text = str(data.get("text") or "")
    message = str(data.get("message") or "Insert via line tool").strip()
    try:
        after_line = int(data.get("after_line", -1))
    except (ValueError, TypeError):
        return "(error: after_line must be an integer)"
    if not repo or not path or not branch or after_line < 0 or not text:
        return "(error: repo, path, branch, after_line>=0, and text required)"
    owner, repo_name = split_repo(repo)

    body, sha_or_err = await _fetch_body_sha(
        ctx, owner, repo_name, path, branch
    )
    if body is None:
        return f"(error: could not read file: {sha_or_err})"
    sha = sha_or_err

    body_lf = body.replace("\r\n", "\n")
    lines = body_lf.split("\n")
    if after_line > len(lines):
        return (
            f"(error: after_line={after_line} exceeds file length "
            f"({len(lines)} lines). Use 0 to prepend or {len(lines)} to append.)"
        )
    payload = text if text.endswith("\n") else text + "\n"
    payload_lines = payload.rstrip("\n").split("\n")
    new_lines = lines[:after_line] + payload_lines + lines[after_line:]
    new_body_lf = "\n".join(new_lines)
    new_body = (
        new_body_lf.replace("\n", "\r\n")
        if "\r\n" in body and "\r\n" not in body_lf
        else new_body_lf
    )
    result = await _commit_with_sha_retry(
        ctx, owner, repo_name, path, branch, new_body, message, sha
    )
    if result.startswith("(error") or result.startswith("Error"):
        return result
    return (
        f"Successfully inserted {len(payload_lines)} line(s) after line "
        f"{after_line}. {result}"
    )


@tool(
    name="read_github_file_meta",
    description=(
        "Cheap pre-flight on a file before reading it. Returns size in chars, line count, "
        "blob SHA, and (for .py files) a top-level symbol map (classes, functions, line "
        "numbers). Use this BEFORE read_github_file when a file might be large — if size "
        ">12000 you'd otherwise need offset pagination. The symbol map lets you jump "
        "straight to a function with read_github_file(start_line=N) instead of reading "
        "the file from the top."
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
            "branch": {
                "type": "string",
                "description": "Branch (default: main)",
            },
        },
        "required": ["repo", "path"],
    },
    wrap_errors=True,
    is_github=True,
)
async def read_github_file_meta(ctx, data):
    over = bump_budget(ctx.context, "explore")
    if over:
        return over
    repo = str(data.get("repo") or "").strip()
    path = str(data.get("path") or "").strip()
    branch = str(data.get("branch") or "main").strip()
    if not repo or not path:
        return "(error: repo and path required)"
    owner, repo_name = split_repo(repo)
    body, sha_or_err = await _fetch_body_sha(
        ctx, owner, repo_name, path, branch
    )
    if body is None:
        return f"(error: {sha_or_err})"
    sha = sha_or_err
    line_count = body.count("\n") + (0 if body.endswith("\n") else 1)
    size = len(body)
    header = f"size: {size} chars, lines: {line_count}, sha: {sha or '?'}"
    if not path.endswith(".py"):
        return header
    # Cap AST parsing input — defends against pathological inputs (deeply nested
    # expressions can hit CPython's recursion limit, very large files burn CPU).
    # ast.parse itself never executes code; it only builds a syntax tree, so this
    # is safe to call on arbitrary GitHub content. The cap is purely DoS hygiene.
    if size > 500_000:
        return f"{header}\n(symbol map skipped: file too large for AST parse)"
    try:
        tree = ast.parse(body)
    except SyntaxError as e:
        return f"{header}\n(symbol map skipped: SyntaxError {e.msg} at line {e.lineno})"
    except (RecursionError, ValueError, MemoryError) as e:
        return (
            f"{header}\n(symbol map skipped: {type(e).__name__} during parse)"
        )
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(f"L{node.lineno}: class {node.name}")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(f"L{sub.lineno}:   def {sub.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"L{node.lineno}: def {node.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    symbols.append(f"L{node.lineno}: {target.id} (constant)")
    if not symbols:
        return f"{header}\n(no top-level symbols)"
    return f"{header}\nTop-level symbols:\n" + "\n".join(symbols)


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


@tool(
    name="create_github_issue",
    description=(
        "Open a new GitHub issue in any repo. Use for bug reports, feature requests, "
        "or tracking work. Pass labels/assignees as arrays of strings when needed. "
        "Returns the issue URL — report it to the user."
    ),
    schema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Target repo in 'owner/repo' format (any repo, not just h4ks-com)",
            },
            "title": {"type": "string", "description": "Issue title"},
            "body": {"type": "string", "description": "Issue body in markdown"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of label names to apply",
            },
            "assignees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of GitHub usernames to assign",
            },
        },
        "required": ["repo", "title"],
    },
    wrap_errors=True,
    is_github=True,
)
async def create_github_issue(ctx, data):
    repo = str(data.get("repo") or "").strip()
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    labels = data.get("labels") or []
    assignees = data.get("assignees") or []
    if not repo or not title:
        return "(error: repo and title required)"
    owner, name = split_repo(repo)
    args: dict = {
        "method": "create",
        "owner": owner,
        "repo": name,
        "title": title,
    }
    if body:
        args["body"] = body
    if isinstance(labels, list) and labels:
        args["labels"] = [str(x) for x in labels]
    if isinstance(assignees, list) and assignees:
        args["assignees"] = [str(x) for x in assignees]
    return await mcp_call(ctx.context, "issue_write", args)
