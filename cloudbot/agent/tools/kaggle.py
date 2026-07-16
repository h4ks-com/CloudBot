"""Kaggle notebook tools — run code on Kaggle's free CPU/GPU and keep a local
index of the notebooks the agent owns.

The table is declared at import time so SQLAlchemy registers it on the global
metadata alongside the other CloudBot tables (same pattern as agent_memory).
It records what each notebook is FOR, which the Kaggle API cannot tell us.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import TypedDict, cast

from sqlalchemy import Column, Integer, String, Table, Text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from cloudbot.agent import kaggle_client
from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.agent.runs import record_run
from cloudbot.bot import CloudBot
from cloudbot.util import database, web

logger = logging.getLogger("cloudbot")

_NOTEBOOKS_TABLE = Table(
    "kaggle_notebooks",
    database.metadata,
    Column("ref", String(120), primary_key=True),
    Column("title", String(200)),
    Column("description", Text),
    Column("url", String(300)),
    Column("gpu", Integer, server_default="0"),
    Column("last_status", String(40)),
    Column("last_version", Integer, server_default="0"),
    Column("channel", String(100)),
    Column("nick", String(100)),
    Column("created_at", String(32)),
    Column("updated_at", String(32)),
    extend_existing=True,
)

_LOG_TAIL_MAX = 1500
_LIST_LIMIT = 25
_CODE_MAX = 60000

_USER_BUCKET = "kaggle-push-user"


@dataclass
class _Launch:
    """A push we made, tracked until it is known to have stopped.

    A run cannot be cancelled, so `expires` is the one thing we can be sure of:
    Kaggle kills the notebook at its session timeout, so past that it is gone
    even if we never saw a terminal status.
    """

    ref: str
    expires: float
    done: bool = False


_launches: dict[str, _Launch] = {}


def _prune_launches() -> None:
    now = time.monotonic()
    for slug, launch in list(_launches.items()):
        if launch.done or now >= launch.expires:
            del _launches[slug]


def _active_count() -> int:
    _prune_launches()
    return len(_launches)


def _mark_done(ref: str) -> None:
    for slug, launch in list(_launches.items()):
        if launch.ref == ref:
            launch.done = True
            del _launches[slug]


def ensure_kaggle_table(engine: Engine) -> None:
    """Create the kaggle_notebooks table if absent (idempotent, for fresh DBs)."""
    _NOTEBOOKS_TABLE.create(bind=engine, checkfirst=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(
    ref: str,
    title: str,
    description: str,
    url: str,
    gpu: bool,
    version: int,
    channel: str,
    nick: str,
) -> None:
    db = database.Session()
    now = _now()
    values = {
        "ref": ref,
        "title": title,
        "description": description,
        "url": url,
        "gpu": 1 if gpu else 0,
        "last_status": "queued",
        "last_version": version,
        "channel": channel,
        "nick": nick,
        "created_at": now,
        "updated_at": now,
    }
    stmt = (
        sqlite_insert(_NOTEBOOKS_TABLE)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["ref"],
            set_={
                "title": title,
                "description": description,
                "url": url,
                "gpu": 1 if gpu else 0,
                "last_status": "queued",
                "last_version": version,
                "updated_at": now,
            },
        )
    )
    try:
        db.execute(stmt)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _mark_status(ref: str, state: str) -> None:
    db = database.Session()
    try:
        db.execute(
            _NOTEBOOKS_TABLE.update()
            .where(_NOTEBOOKS_TABLE.c.ref == ref)
            .values(last_status=state, updated_at=_now())
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _forget(ref: str) -> None:
    db = database.Session()
    try:
        db.execute(
            _NOTEBOOKS_TABLE.delete().where(_NOTEBOOKS_TABLE.c.ref == ref)
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


class NotebookRow(TypedDict):
    ref: str
    title: str
    description: str
    url: str
    gpu: int
    last_status: str
    last_version: int
    channel: str
    nick: str
    created_at: str
    updated_at: str


def list_notebooks(channel: str = "") -> list[NotebookRow]:
    db = database.Session()
    query = _NOTEBOOKS_TABLE.select().order_by(
        _NOTEBOOKS_TABLE.c.updated_at.desc()
    )
    if channel:
        query = query.where(_NOTEBOOKS_TABLE.c.channel == channel)
    rows = db.execute(query.limit(_LIST_LIMIT)).mappings().fetchall()
    return [cast(NotebookRow, dict(row)) for row in rows]


@dataclass(frozen=True)
class KaggleConfig:
    """Parsed `plugins.kaggle_agent` config. Every default here is live — the
    repo ships no kaggle_agent block."""

    default_timeout_s: int = 900
    max_timeout_s: int = 3600
    wait_s: int = 90
    min_gpu_reserve_h: float = 2.0
    max_concurrent: int = 3
    user_per_minute: int = 2


def _config(bot: CloudBot) -> KaggleConfig:
    raw = (bot.config.get("plugins") or {}).get("kaggle_agent") or {}
    if not isinstance(raw, dict):
        return KaggleConfig()
    fallback = KaggleConfig()

    def as_int(key: str, default: int) -> int:
        value = raw.get(key, default)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool)
            else default
        )

    def as_float(key: str, default: float) -> float:
        value = raw.get(key, default)
        return (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else default
        )

    return KaggleConfig(
        default_timeout_s=as_int(
            "default_timeout_s", fallback.default_timeout_s
        ),
        max_timeout_s=as_int("max_timeout_s", fallback.max_timeout_s),
        wait_s=as_int("wait_s", fallback.wait_s),
        min_gpu_reserve_h=as_float(
            "min_gpu_reserve_h", fallback.min_gpu_reserve_h
        ),
        max_concurrent=as_int("max_concurrent", fallback.max_concurrent),
        user_per_minute=as_int("user_per_minute", fallback.user_per_minute),
    )


def _timeout_for(cfg: KaggleConfig, requested: int | None) -> int:
    """The only ceiling on a runaway notebook: Kaggle exposes no usable cancel,
    so a hung run bills until this fires. Clamped here rather than trusted from
    the model."""
    return max(60, min(requested or cfg.default_timeout_s, cfg.max_timeout_s))


def _limits(cfg: KaggleConfig, nick: str) -> str | None:
    """Two brakes, no quotas: a cap on runs in flight at once, and a burst limit
    so one person cannot spam pushes.

    Returns the message of the limit hit, or None. Nothing is recorded here —
    the caller records only after Kaggle accepts the push, so a rejected push
    does not burn anyone's allowance.
    """
    # plugins/ is the consumer layer; importing it at module scope would invert
    # the dependency and risk a cycle, so every cloudbot/ module defers it.
    # pylint: disable=import-outside-toplevel
    from plugins.ratelimit import Limit, check

    active = _active_count()
    if active >= cfg.max_concurrent:
        return (
            f"(error: {active} notebooks already running and the limit is "
            f"{cfg.max_concurrent} — runs cannot be cancelled, so wait for one "
            f"to finish)"
        )

    return check(
        database.Session(),
        f"{_USER_BUCKET}:{nick.lower()}",
        [
            Limit(
                60,
                cfg.user_per_minute,
                f"(error: max {cfg.user_per_minute} notebooks a minute — "
                f"wait a moment)",
            )
        ],
    )


def _record_usage(nick: str) -> None:
    # pylint: disable=import-outside-toplevel
    from plugins.ratelimit import record

    record(database.Session(), f"{_USER_BUCKET}:{nick.lower()}")


def format_quota(report: kaggle_client.QuotaReport) -> str:
    """Shared by the kaggle_quota tool and the .kquota command."""
    return (
        f"GPU: {report.gpu.remaining_h:.1f}h left of {report.gpu.total_h:.0f}h "
        f"(used {report.gpu.used_h:.1f}h, reserved {report.gpu.reserved_h:.1f}h) | "
        f"TPU: {report.tpu.remaining_h:.1f}h left of {report.tpu.total_h:.0f}h | "
        f"resets {report.refresh_at}"
    )


@tool(
    name="kaggle_quota",
    description=(
        "Check remaining weekly Kaggle accelerator quota (GPU and TPU hours). "
        "CPU notebooks do NOT consume this quota — only GPU/TPU do. "
        "Check this before running a GPU notebook."
    ),
    schema={"type": "object", "properties": {}},
    wrap_errors=True,
)
async def kaggle_quota(ctx, data) -> str:
    event = ctx.context
    try:
        token = kaggle_client.token_from_bot(event.bot)
        report = await run_in_executor(kaggle_client.quota, token)
    except kaggle_client.KaggleError as e:
        return f"(error: {e})"
    return format_quota(report)


@tool(
    name="kaggle_run_notebook",
    description=(
        "Create (or update) a Kaggle notebook and RUN it, then wait briefly for "
        "the result. Pushing IS running — there is no separate run step, and "
        "re-running the same title just makes a new version of the same notebook.\n"
        "The whole notebook is ONE program: you always send the complete source "
        "and Kaggle always runs all of it top to bottom. There is no way to edit "
        "or run a single cell, so to change anything, resend the full code.\n"
        "Write plain Python. Save any artifact you want to keep to /kaggle/working/ "
        "— files there are retrievable afterwards, and they survive even if the run "
        "is killed by the timeout. stdout/stderr are captured as the run log.\n"
        "Artifacts are auto-uploaded to the paste service and returned as links, "
        "but ONLY up to 25MB each — anything bigger cannot be shared and stays on "
        "the notebook page, so prefer writing small outputs (a summary, a plot, a "
        "JSON) over dumping a huge checkpoint if the user needs to see it.\n"
        "CPU is free and unmetered; set gpu=true ONLY for real GPU work (it burns "
        "a 30h/week quota — check kaggle_quota first). Set internet=true if the "
        "code must reach the network: pip install, downloads, OR reading an input "
        "file you already have a URL for (e.g. an s.h4ks.com paste) — without it "
        "the notebook has no network at all and those fetches fail.\n"
        "If the run is still going when the wait elapses, you get a ref back — "
        "poll it with kaggle_notebook_status and then kaggle_notebook_output."
    ),
    schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short notebook title; also its stable slug/URL. Reuse the same title to update an existing notebook.",
            },
            "code": {"type": "string", "description": "Python source to run."},
            "description": {
                "type": "string",
                "description": "What this notebook is for (stored locally so you can find it later).",
            },
            "gpu": {
                "type": "boolean",
                "description": "Request a GPU (T4). Burns weekly quota. Default false.",
            },
            "internet": {
                "type": "boolean",
                "description": "Allow network access from the notebook. Default false.",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Hard cap on the run in seconds. Clamped to the configured maximum.",
            },
            "wait_s": {
                "type": "integer",
                "description": "How long to wait inline for completion before returning a handle.",
            },
        },
        "required": ["title", "code"],
    },
    wrap_errors=True,
)
async def kaggle_run_notebook(ctx, data) -> str:
    event = ctx.context
    title = str(data.get("title", "")).strip()
    code = str(data.get("code", ""))
    if not title:
        return "(error: title required)"
    if not code.strip():
        return "(error: code required)"
    if len(code) > _CODE_MAX:
        return f"(error: code too long, {len(code)} > {_CODE_MAX} chars)"

    cfg = _config(event.bot)
    gpu = bool(data.get("gpu", False))
    internet = bool(data.get("internet", False))
    timeout_s = _timeout_for(cfg, data.get("timeout_s"))
    # wait_s=0 is the caller asking to push and get a handle straight back, so it
    # has to survive as 0 rather than fall through to the default.
    requested_wait = data.get("wait_s")
    if requested_wait is None:
        requested_wait = cfg.wait_s
    wait_s = max(0, min(int(requested_wait), 600))

    try:
        token = kaggle_client.token_from_bot(event.bot)
    except kaggle_client.KaggleNotConfigured as e:
        return f"(error: {e})"

    slug = kaggle_client.slugify(title)
    _prune_launches()
    already = _launches.get(slug)
    if already:
        owner, _, _ = already.ref.partition("/")
        last = _remember(
            event, f"https://www.kaggle.com/code/{owner}/{slug}", already.ref
        )
        state = await _poll(token, already.ref, wait_s)
        if state in kaggle_client.TERMINAL_STATES:
            _mark_done(already.ref)
        return (
            f"'{title}' was already launched and is {state} — ref "
            f"'{already.ref}'. Not pushing again (a run cannot be cancelled). "
            + (
                await _result_text(token, already.ref, state, last)
                if state in kaggle_client.TERMINAL_STATES
                else "Poll kaggle_notebook_status."
            )
        )

    nick = getattr(event, "nick", "") or "?"
    blocked: str | None = await run_in_executor(partial(_limits, cfg, nick))
    if blocked:
        return blocked

    if gpu:
        try:
            report = await run_in_executor(kaggle_client.quota, token)
        except kaggle_client.KaggleError as e:
            return f"(error checking quota: {e})"
        reserve_h = cfg.min_gpu_reserve_h
        if report.gpu.remaining_h < reserve_h:
            return (
                f"(error: only {report.gpu.remaining_h:.1f}h GPU quota left, below the "
                f"{reserve_h:.1f}h reserve — run on CPU instead, or wait for reset "
                f"at {report.refresh_at})"
            )

    try:
        pushed = await run_in_executor(
            lambda: kaggle_client.push(
                token,
                slug=slug,
                title=title,
                code=code,
                session_timeout_s=timeout_s,
                is_private=False,
                enable_gpu=gpu,
                enable_internet=internet,
                machine_shape="NvidiaTeslaT4" if gpu else None,
            )
        )
    except kaggle_client.KaggleError as e:
        return f"(error pushing notebook: {e})"

    _launches[slug] = _Launch(
        ref=pushed.ref, expires=time.monotonic() + timeout_s
    )
    await run_in_executor(partial(_record_usage, nick))
    await run_in_executor(
        partial(
            _record,
            pushed.ref,
            title,
            str(data.get("description", "")).strip(),
            pushed.url,
            gpu,
            pushed.version,
            getattr(event, "chan", "") or "",
            nick,
        )
    )

    ref = pushed.ref
    last = _remember(event, pushed.url, ref)
    state = await _poll(token, ref, wait_s)
    if state in kaggle_client.TERMINAL_STATES:
        _mark_done(ref)
    await run_in_executor(partial(_mark_status, ref, state))
    head = (
        f"{pushed.url} (v{pushed.version}, {'GPU' if gpu else 'CPU'}, "
        f"cap {timeout_s}s)"
    )
    # The main agent injects recent runs into its own instructions, so a
    # follow-up ("make that notebook faster") can find this one; detail carries
    # the source so it can be edited rather than rewritten from scratch.
    record_run(
        getattr(event, "chan", "") or "",
        "notebook",
        f"{title} — {str(data.get('description', '')).strip() or state}",
        pushed.url,
        detail=code,
    )
    if state not in kaggle_client.TERMINAL_STATES:
        return (
            f"Started: {head}\nStill {state} after {wait_s}s — ref '{ref}'. "
            f"Poll kaggle_notebook_status, then kaggle_notebook_output."
        )
    return f"Finished ({state}): {head}\n" + await _result_text(
        token, ref, state, last
    )


async def _poll(token: str, ref: str, wait_s: int) -> str:
    """Poll until terminal or wait_s elapses.

    Deadline is wall clock, not the sum of sleeps: each tick also makes an HTTP
    call that can take up to the client's own timeout, so counting only sleeps
    would overshoot wait_s several-fold. A transient API error is retried rather
    than treated as an answer — the caller reports the returned state as fact.
    """
    deadline = time.monotonic() + wait_s
    delay = 3.0
    state = kaggle_client.KernelState.QUEUED.value
    while True:
        try:
            state = await run_in_executor(kaggle_client.status, token, ref)
            if state in kaggle_client.TERMINAL_STATES:
                return state
        except kaggle_client.KaggleError as e:
            logger.debug("kaggle: status poll for %s failed: %s", ref, e)
        if time.monotonic() >= deadline:
            return state
        await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 1.5, 20.0)


@dataclass
class LastRun:
    """What the run actually produced, captured from the tool's own results.

    The reply is assembled from this rather than from the model's prose: models
    retype URLs and corrupt them, and a notebook nobody can open is worthless.
    """

    url: str
    ref: str
    artifacts: list[str]


def _remember(event: object, url: str, ref: str) -> LastRun:
    last = LastRun(url=url, ref=ref, artifacts=[])
    setattr(event, "_kaggle_last", last)
    return last


def last_run(event: object) -> LastRun | None:
    value = getattr(event, "_kaggle_last", None)
    return value if isinstance(value, LastRun) else None


async def _artifact_links(files: list[kaggle_client.OutputFile]) -> list[str]:
    """Upload every artifact to the paste service. Shared by the run and output
    tools so a finished run always carries shareable links."""
    lines = []
    for item in files:
        try:
            blob = await run_in_executor(kaggle_client.fetch_file, item.url)
        except kaggle_client.ArtifactTooLarge as e:
            lines.append(
                f"{item.name} (not shared, {e} — it is still on the notebook)"
            )
            continue
        except kaggle_client.KaggleError as e:
            lines.append(f"{item.name} (download failed: {e})")
            continue
        ext = item.name.rsplit(".", 1)[-1] if "." in item.name else "txt"
        try:
            # Without raise_on_no_paste, web.paste returns its failure message as
            # a plain string, which would be reported here as if it were a URL.
            url = await run_in_executor(
                partial(web.paste, blob, ext, raise_on_no_paste=True)
            )
        except (OSError, ValueError, web.NoPasteException) as e:
            lines.append(f"{item.name} (upload failed: {e})")
            continue
        lines.append(f"{item.name}: {url}")
    return lines


async def _result_text(
    token: str, ref: str, state: str, last: LastRun | None = None
) -> str:
    try:
        files, log = await run_in_executor(kaggle_client.output, token, ref)
    except kaggle_client.KaggleError as e:
        return f"(error fetching output: {e})"
    parts = []
    if state == kaggle_client.KernelState.ERROR:
        try:
            reason = await run_in_executor(
                kaggle_client.failure_message, token, ref
            )
        except kaggle_client.KaggleError:
            reason = ""
        if reason:
            parts.append(f"failure: {reason}")
    if state == kaggle_client.KernelState.CANCEL_ACKNOWLEDGED:
        parts.append(
            "run hit its timeout and was killed (artifacts below are partial)"
        )
    if files:
        links = await _artifact_links(files)
        if last is not None:
            last.artifacts = links
        parts.append("artifacts:\n" + "\n".join(f"- {line}" for line in links))
    if log:
        tail = log[-_LOG_TAIL_MAX:]
        parts.append(f"log:\n{tail}")
    return "\n".join(parts) or "(no output)"


@tool(
    name="kaggle_notebook_status",
    description=(
        "Check a Kaggle notebook run's state. Terminal states: complete, error, "
        "cancelacknowledged (= killed by its timeout; partial artifacts still exist)."
    ),
    schema={
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Notebook ref, 'owner/slug'.",
            }
        },
        "required": ["ref"],
    },
    wrap_errors=True,
)
async def kaggle_notebook_status(ctx, data) -> str:
    event = ctx.context
    ref = str(data.get("ref", "")).strip()
    if not ref:
        return "(error: ref required)"
    try:
        token = kaggle_client.token_from_bot(event.bot)
        state = await run_in_executor(kaggle_client.status, token, ref)
    except kaggle_client.KaggleError as e:
        return f"(error: {e})"
    done = state in kaggle_client.TERMINAL_STATES
    if done:
        _mark_done(ref)
    await run_in_executor(partial(_mark_status, ref, state))
    return f"{ref}: {state}" + ("" if done else " (still running)")


@tool(
    name="kaggle_notebook_output",
    description=(
        "Fetch a finished notebook's artifacts and run log. Uploads each artifact "
        "to the paste service and returns shareable URLs, plus the tail of the log. "
        "Works even for runs killed by their timeout — those keep whatever they "
        "had already written to /kaggle/working/."
    ),
    schema={
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Notebook ref, 'owner/slug'.",
            },
            "upload": {
                "type": "boolean",
                "description": "Upload artifacts to the paste service and return URLs. Default true.",
            },
        },
        "required": ["ref"],
    },
    wrap_errors=True,
)
async def kaggle_notebook_output(ctx, data) -> str:
    event = ctx.context
    ref = str(data.get("ref", "")).strip()
    if not ref:
        return "(error: ref required)"
    upload = bool(data.get("upload", True))
    try:
        token = kaggle_client.token_from_bot(event.bot)
        files, log = await run_in_executor(kaggle_client.output, token, ref)
    except kaggle_client.KaggleError as e:
        return f"(error: {e})"

    lines = []
    if not files:
        lines.append("no artifacts (nothing written to /kaggle/working/)")
    elif upload:
        lines.extend(f"- {line}" for line in await _artifact_links(files))
    else:
        lines.extend(f"- {item.name}" for item in files)
    if log:
        lines.append(f"log:\n{log[-_LOG_TAIL_MAX:]}")
    return "\n".join(lines)


@tool(
    name="kaggle_list_notebooks",
    description=(
        "List the Kaggle notebooks this bot has created, newest first, with what "
        "each one is for and its last known run state. Use this to find and reuse "
        "an existing notebook instead of making a near-duplicate."
    ),
    schema={
        "type": "object",
        "properties": {
            "this_channel_only": {
                "type": "boolean",
                "description": "Only notebooks made in this channel. Default false.",
            }
        },
    },
    wrap_errors=True,
)
async def kaggle_list_notebooks(ctx, data) -> str:
    event = ctx.context
    channel = (
        (getattr(event, "chan", "") or "")
        if bool(data.get("this_channel_only", False))
        else ""
    )
    rows = await run_in_executor(list_notebooks, channel)
    if not rows:
        return "No notebooks yet."
    lines = []
    for notebook in rows:
        desc = (
            f" — {notebook['description']}"
            if notebook.get("description")
            else ""
        )
        accelerator = "GPU" if notebook.get("gpu") else "CPU"
        lines.append(
            f"- {notebook['ref']} [{accelerator}, "
            f"{notebook.get('last_status') or '?'}]{desc}\n"
            f"  {notebook.get('url') or ''}"
        )
    return "\n".join(lines)


@tool(
    name="kaggle_delete_notebook",
    description="Delete a Kaggle notebook and drop it from the local index.",
    schema={
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Notebook ref, 'owner/slug'.",
            }
        },
        "required": ["ref"],
    },
    wrap_errors=True,
)
async def kaggle_delete_notebook(ctx, data) -> str:
    event = ctx.context
    ref = str(data.get("ref", "")).strip()
    if not ref:
        return "(error: ref required)"
    try:
        token = kaggle_client.token_from_bot(event.bot)
        await run_in_executor(kaggle_client.delete, token, ref)
    except kaggle_client.KaggleError as e:
        return f"(error: {e})"
    _mark_done(ref)
    await run_in_executor(partial(_forget, ref))
    return f"Deleted {ref}."
