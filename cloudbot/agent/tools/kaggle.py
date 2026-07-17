"""Kaggle notebook tools — run code on Kaggle's free CPU/GPU and keep a local
index of the notebooks the agent owns.

The table is declared at import time so SQLAlchemy registers it on the global
metadata alongside the other CloudBot tables (same pattern as agent_memory).
It records what each notebook is FOR, which the Kaggle API cannot tell us.
"""

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import TypedDict, cast

from sqlalchemy import Column, Integer, String, Table, Text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cloudbot.agent import kaggle_client
from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.agent.runs import record_run
from cloudbot.bot import CloudBot
from cloudbot.util import colors, database, web

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
# Enough for the agent to spot the notebook a request means; the full list is a
# tool call away and every line here is prompt on every run.
_CONTEXT_MAX = 10
_CODE_MAX = 60000
_ARTIFACT_LIST_MAX = 40


_USER_BUCKET = "kaggle-push-user"
_URL_RE = re.compile(r"(https?://\S+)")
# sessionTimeoutSeconds starts when the notebook leaves Kaggle's queue, so a run
# outlives push+timeout by however long it waited.
_QUEUE_GRACE_S = 600
# Kaggle spends the first seconds of a session starting the container, so a
# shorter cap than this kills the notebook before its code runs at all.
_MIN_TIMEOUT_S = 60


@dataclass
class _Launch:
    """A push we made, tracked until it is known to have stopped.

    A run cannot be cancelled, so `expires` is the one thing we can be sure of.
    It allows for queue time on top of the session cap: sessionTimeoutSeconds
    starts when the notebook leaves the queue, and forgetting a run early would
    let max_concurrent admit an extra one.
    """

    ref: str
    expires: float


# Written from the event loop and pruned from executor threads (_limits runs in
# one), and a dict mutated mid-iteration raises — same reason runs.py locks.
_launches: dict[str, _Launch] = {}
_LAUNCHES_LOCK = threading.Lock()


def _prune_launches() -> None:
    now = time.monotonic()
    with _LAUNCHES_LOCK:
        for slug in [s for s, l in _launches.items() if now >= l.expires]:
            _launches.pop(slug, None)


def _active_count() -> int:
    _prune_launches()
    with _LAUNCHES_LOCK:
        return len(_launches)


def _mark_done(ref: str) -> None:
    with _LAUNCHES_LOCK:
        for slug in [s for s, l in _launches.items() if l.ref == ref]:
            _launches.pop(slug, None)


def running_refs() -> list[str]:
    """Refs pushed by this process that have not passed their timeout yet.

    The stored last_status cannot answer this: it is only as fresh as the last
    poll, and a run that nobody waited on keeps its 'queued' forever.
    """
    _prune_launches()
    with _LAUNCHES_LOCK:
        return sorted({launch.ref for launch in _launches.values()})


@contextmanager
def _session() -> Iterator[Session]:
    """A SQLite session that always ends its transaction and releases the thread.

    Every DB call here runs in a `run_in_executor` thread, and `database.Session`
    is thread-local — so consecutive calls land on different threads holding
    different connections. SQLite allows one writer, so a transaction left open on
    one makes the next thread's write fail with "database is locked" — and a raw
    `database.Session()` here has no lifecycle owner to close it, unlike a hook's
    session. (`ratelimit.check` leaves its prune uncommitted for exactly that
    reason: it expects its caller to own the transaction boundary.)
    """
    db = database.Session()
    try:
        yield db
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    finally:
        database.Session.remove()


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
    now = _now()
    values = {
        "ref": ref,
        "title": title,
        "description": description,
        "url": url,
        "gpu": 1 if gpu else 0,
        "last_status": kaggle_client.KernelState.QUEUED.value,
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
                "last_status": kaggle_client.KernelState.QUEUED.value,
                "last_version": version,
                "updated_at": now,
            },
        )
    )
    with _session() as db:
        db.execute(stmt)


def _mark_status(ref: str, state: str) -> None:
    with _session() as db:
        db.execute(
            _NOTEBOOKS_TABLE.update()
            .where(_NOTEBOOKS_TABLE.c.ref == ref)
            .values(last_status=state, updated_at=_now())
        )


def _forget(ref: str) -> None:
    with _session() as db:
        db.execute(
            _NOTEBOOKS_TABLE.delete().where(_NOTEBOOKS_TABLE.c.ref == ref)
        )


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


def list_notebooks(
    channel: str = "", search: str = "", limit: int = _LIST_LIMIT
) -> list[NotebookRow]:
    """Notebooks this bot owns, newest first.

    `search` matches the ref, title or description — the description is the only
    record of what a notebook is FOR, so it is the useful thing to search.
    """
    query = _NOTEBOOKS_TABLE.select().order_by(
        _NOTEBOOKS_TABLE.c.updated_at.desc()
    )
    if channel:
        query = query.where(_NOTEBOOKS_TABLE.c.channel == channel)
    if search:
        like = f"%{search}%"
        query = query.where(
            _NOTEBOOKS_TABLE.c.ref.ilike(like)
            | _NOTEBOOKS_TABLE.c.title.ilike(like)
            | _NOTEBOOKS_TABLE.c.description.ilike(like)
        )
    with _session() as db:
        rows = db.execute(query.limit(limit)).mappings().fetchall()
        return [cast(NotebookRow, dict(row)) for row in rows]


@dataclass(frozen=True)
class KaggleConfig:
    """Parsed `plugins.kaggle_agent` config. Every default here is live — the
    repo ships no kaggle_agent block."""

    default_timeout_s: int = 900
    max_timeout_s: int = 1800
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
    so a hung run bills until this fires. Capped here rather than trusted from
    the model, and never 0 — that is proto3's default, which the server would
    read as "unset" and fall back to its own 12h limit.
    """
    wanted = int(requested or 0)
    if wanted <= 0:
        wanted = cfg.default_timeout_s
    return max(_MIN_TIMEOUT_S, min(wanted, cfg.max_timeout_s))


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

    with _session() as db:
        return check(
            db,
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

    with _session() as db:
        record(db, f"{_USER_BUCKET}:{nick.lower()}")


def format_notebooks(rows: list[NotebookRow]) -> str:
    """Shared by the kaggle_list_notebooks tool and the .knotebooks command."""
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
            f"{colors.parse('$(bold)' + notebook['ref'] + '$(clear)')} "
            f"[{accelerator}, {notebook.get('last_status') or '?'}]{desc}\n"
            f"  {notebook.get('url') or ''}"
        )
    return "\n".join(lines)


def _parse_cells(raw: object) -> list[kaggle_client.Cell] | str:
    """Model-supplied cells as typed Cells, or the error to hand back to it.

    Returns the message rather than raising so a malformed call costs the model
    one turn and a fixable sentence, not the whole run.
    """
    if not isinstance(raw, list) or not raw:
        return (
            "(error: cells required — a list of {type, source} objects, e.g. "
            '[{"type": "markdown", "source": "# What this does"}, '
            '{"type": "code", "source": "print(1)"}])'
        )
    cells: list[kaggle_client.Cell] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return f"(error: cell {index} is not an object with 'type' and 'source')"
        kind = str(item.get("type", "")).strip().lower()
        source = str(item.get("source", ""))
        if kind not in ("markdown", "code"):
            return (
                f"(error: cell {index} type must be 'markdown' or 'code', "
                f"got {kind!r})"
            )
        if not source.strip():
            return f"(error: cell {index} has an empty source)"
        cells.append(
            kaggle_client.Cell("code" if kind == "code" else "markdown", source)
        )
    if not any(cell.kind == "code" for cell in cells):
        return "(error: notebook has no code cell, so it would do nothing)"
    return cells


def notebook_context() -> str:
    """What already exists and what is still in flight, as a prompt block.

    Injected on every run so the agent starts knowing both: discovering it costs
    turns a run cannot spare, and nothing else can tell it a notebook is mid-run,
    which matters because a Kaggle run cannot be cancelled and a second one just
    spends quota racing the first.

    Ambient context is worth less than the run it decorates, so a database that
    will not answer costs the caller this block rather than its whole agent run.
    """
    try:
        rows = list_notebooks(limit=_CONTEXT_MAX)
    except SQLAlchemyError:
        logger.exception("kaggle: notebook context lookup failed")
        return ""
    live = set(running_refs())
    if not rows:
        return ""
    lines = ["[your Kaggle notebooks — reference only, NOT a task to continue]"]
    for row in rows:
        state = (
            "RUNNING NOW"
            if row["ref"] in live
            else (row.get("last_status") or "?")
        )
        desc = f" — {row['description']}" if row.get("description") else ""
        accelerator = "GPU" if row.get("gpu") else "CPU"
        lines.append(f"- {row['ref']} [{accelerator}, {state}]{desc}")
    lines.append("[end notebooks]\n")
    return "\n".join(lines)


def format_quota(report: kaggle_client.QuotaReport) -> str:
    """Render weekly quota for chat.

    Two decimals because a whole notebook run is minutes: at one decimal any run
    under ~3 minutes reads as 0.0h and the counter looks broken.
    """
    return (
        f"GPU: {report.gpu.remaining_h:.2f}h left of {report.gpu.total_h:.0f}h "
        f"(used {report.gpu.used_h:.2f}h, reserved {report.gpu.reserved_h:.2f}h) | "
        f"TPU: {report.tpu.remaining_h:.2f}h left of {report.tpu.total_h:.0f}h | "
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
        "You always send the COMPLETE list of cells and Kaggle runs all of them top "
        "to bottom in one fresh container. There is no way to run or re-run a single "
        "cell, and nothing carries over between runs, so to change anything resend "
        "every cell.\n"
        "Cells are how the notebook reads to whoever opens its link, so write it like "
        "something you'd publish: a markdown cell introducing it, a short markdown "
        "cell before each step, and small single-purpose code cells. This also pays "
        "off on failure — the error names the cell it happened in ('Exception "
        "encountered at In [2]'), so small cells point straight at the problem.\n"
        "Make it narrate itself. You can read a run's output while it is still going, "
        "but only what it actually prints, so a silent notebook is one you cannot "
        "debug and cannot tell apart from a hung one. Print before and after anything "
        "slow, print what you are about to rely on (device, versions, file sizes, "
        "paths), and never silence the noisy parts — no `pip install -q`, no discarded "
        "stderr. Assume every run is one you will have to diagnose from its log alone.\n"
        "Save any artifact you want to keep to /kaggle/working/ "
        "— files there are retrievable afterwards, and they survive even if the run "
        "is killed by the timeout. stdout/stderr are captured as the run log.\n"
        "Every run starts from a CLEAN container, so pip installs, git clones and "
        "model downloads all repeat and are usually most of the runtime. When setup "
        "is heavy, do it ONCE in its own notebook that writes to /kaggle/working/ "
        "(`pip download <pkgs> -d /kaggle/working/wheels`, clone into it, point "
        "HF_HOME at it), then pass kernel_sources=['owner/slug'] on the runs that "
        "iterate: that notebook's output mounts read-only at "
        "/kaggle/input/notebooks/<owner>/<slug>/, and you install from it with "
        "`pip install --no-index --find-links=/kaggle/input/notebooks/<owner>/<slug>/wheels`. "
        "This turns a 6-minute edit-test cycle into seconds and is how you avoid "
        "burning GPU quota re-downloading the same weights on every fix.\n"
        "Nothing is published automatically except the notebook link. To give the "
        "user a file, call kaggle_notebook_output with `share=<path>` — pick the "
        "one or two files they actually asked for. /kaggle/working/ is also the "
        "working directory, so clone repos and install packages under /tmp "
        "instead, or the outputs get buried.\n"
        "CPU is free and unmetered; set gpu=true ONLY for real GPU work (it burns "
        "a 30h/week quota — check kaggle_quota first). Set internet=true if the "
        "code must reach the network: pip install, downloads, OR reading an input "
        "file you already have a URL for (e.g. an s.h4ks.com paste) — without it "
        "the notebook has no network at all and those fetches fail.\n"
        "If the run is still going when the wait elapses you get a ref back plus "
        "whatever it has printed so far — wait for it with kaggle_wait_for_notebook, "
        "which blocks until it is done and shows you its live output meanwhile."
    ),
    schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short notebook title; also its stable slug/URL. Reuse the same title to update an existing notebook.",
            },
            "cells": {
                "type": "array",
                "description": "The notebook, cell by cell, in run order. Open with a markdown cell saying what it does, and put a short markdown cell before each step. Keep code cells small and single-purpose — one per step — because a failure is reported by cell number.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["markdown", "code"],
                            "description": "'markdown' for prose, 'code' for Python.",
                        },
                        "source": {
                            "type": "string",
                            "description": "Cell body: markdown text, or Python source. Plain text — never JSON.",
                        },
                    },
                    "required": ["type", "source"],
                },
            },
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
                "description": "Hard cap on the run in seconds (default 900, max 1800). Raise it for genuinely long work — the run is KILLED at this cap and cannot be extended or cancelled, so a job that needs 20 minutes must ask for it up front.",
            },
            "wait_s": {
                "type": "integer",
                "description": "How long to wait inline for completion before returning a handle.",
            },
            "kernel_sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Refs ('owner/slug') of your own notebooks whose output to mount read-only at /kaggle/input/notebooks/<owner>/<slug>/. Use it to reuse a setup notebook's clone/wheels/weights instead of downloading them again.",
            },
        },
        "required": ["title", "cells"],
    },
    wrap_errors=True,
)
async def kaggle_run_notebook(ctx, data) -> str:
    event = ctx.context
    title = str(data.get("title", "")).strip()
    if not title:
        return "(error: title required)"
    cells = _parse_cells(data.get("cells"))
    if isinstance(cells, str):
        return cells
    size = sum(len(cell.source) for cell in cells)
    if size > _CODE_MAX:
        return f"(error: notebook too long, {size} > {_CODE_MAX} chars)"

    cfg = _config(event.bot)
    gpu = bool(data.get("gpu", False))
    internet = bool(data.get("internet", False))
    timeout_s = _timeout_for(cfg, data.get("timeout_s"))
    raw_sources = data.get("kernel_sources")
    kernel_sources = (
        [s.strip() for s in raw_sources if isinstance(s, str) and s.strip()]
        if isinstance(raw_sources, list)
        else []
    )
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
    with _LAUNCHES_LOCK:
        already = _launches.get(slug)
    if already:
        owner, _, _ = already.ref.partition("/")
        _remember(
            event, f"https://www.kaggle.com/code/{owner}/{slug}", already.ref
        )
        state = await _poll(token, already.ref, wait_s)
        if state in kaggle_client.TERMINAL_STATES:
            _mark_done(already.ref)
        return (
            f"'{title}' was already launched and is {state} — ref "
            f"'{already.ref}'. Not pushing again (a run cannot be cancelled). "
            + (
                await _result_text(event, token, already.ref, state, timeout_s)
                if state in kaggle_client.TERMINAL_STATES
                else "Wait for it with kaggle_wait_for_notebook."
            )
        )

    nick = getattr(event, "nick", "") or "?"
    try:
        blocked: str | None = await run_in_executor(partial(_limits, cfg, nick))
    except SQLAlchemyError:
        # A brake we cannot read is not a reason to refuse the user; the
        # concurrency cap above is in-memory and still holds.
        logger.exception("kaggle: rate-limit check failed, allowing")
        blocked = None
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
                cells=cells,
                session_timeout_s=timeout_s,
                is_private=False,
                enable_gpu=gpu,
                enable_internet=internet,
                machine_shape="NvidiaTeslaT4" if gpu else None,
                kernel_sources=kernel_sources,
            )
        )
    except kaggle_client.KaggleError as e:
        return f"(error pushing notebook: {e})"

    # The notebook is live and uncancellable from here on. Bookkeeping must never
    # be able to lose its URL, so every step below is best-effort.
    with _LAUNCHES_LOCK:
        _launches[slug] = _Launch(
            ref=pushed.ref,
            expires=time.monotonic() + timeout_s + _QUEUE_GRACE_S,
        )
    ref = pushed.ref
    _remember(event, pushed.url, ref)
    description = str(data.get("description", "")).strip()
    await _bookkeep(
        partial(_record_usage, nick),
        partial(
            _record,
            ref,
            title,
            description,
            pushed.url,
            gpu,
            pushed.version,
            getattr(event, "chan", "") or "",
            nick,
        ),
    )

    state = await _poll(token, ref, wait_s)
    if state in kaggle_client.TERMINAL_STATES:
        _mark_done(ref)
    await _bookkeep(partial(_mark_status, ref, state))
    head = (
        f"{pushed.url} (v{pushed.version}, {'GPU' if gpu else 'CPU'}, "
        f"cap {timeout_s}s)"
    )
    if pushed.invalid_sources:
        head += (
            f"\nWARNING: no notebook named {', '.join(pushed.invalid_sources)}, "
            f"so it was NOT mounted and this run has none of its cached setup. "
            f"Check the ref against your notebook list."
        )
    # The main agent injects recent runs into its own instructions, so a
    # follow-up ("make that notebook faster") can find this one; detail carries
    # the source so it can be edited rather than rewritten from scratch.
    record_run(
        getattr(event, "chan", "") or "",
        "notebook",
        f"{title} — {description or state}",
        pushed.url,
        # Stored in the shape the tool takes back, so an edit is a change to one
        # cell rather than a rewrite of the notebook from memory.
        detail=json.dumps(
            [{"type": cell.kind, "source": cell.source} for cell in cells]
        ),
    )
    if state not in kaggle_client.TERMINAL_STATES:
        return (
            f"Started: {head}\nStill {state} after {wait_s}s — ref '{ref}'.\n"
            + await _live_text(token, ref)
        )
    return f"Finished ({state}): {head}\n" + await _result_text(
        event, token, ref, state, timeout_s
    )


async def _bookkeep(*steps: Callable[[], None]) -> None:
    """Run local DB writes that must not cost the caller its result.

    Once a notebook is pushed it cannot be cancelled, so failing to record it
    locally is a bad trade for losing the URL of a run that is already burning
    quota: safe_tool would turn the failure into an error string and the URL
    would go with it.
    """
    for step in steps:
        try:
            await run_in_executor(step)
        except SQLAlchemyError:
            logger.exception("kaggle: bookkeeping write failed, continuing")


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
    """What the tools actually produced, captured from their own results.

    Models retype URLs and corrupt them, so the reply is assembled from this
    rather than from the model's prose. `known_urls` is every URL a tool really
    emitted: the reply keeps those and drops the rest, which is what separates a
    link the model read off a tool result from one it invented.
    """

    url: str = ""
    ref: str = ""
    known_urls: set[str] = field(default_factory=set)


def _run_state(event: object) -> LastRun:
    last: LastRun | None = last_run(event)
    if last is None:
        last = LastRun()
        setattr(event, "_kaggle_last", last)
    return last


def _remember(event: object, url: str, ref: str) -> LastRun:
    last = _run_state(event)
    last.url = url
    last.ref = ref
    last.known_urls.add(url)
    return last


def _remember_urls(event: object, text: str) -> None:
    """Register every URL a tool result carried, so the reply can keep them."""
    _run_state(event).known_urls.update(_URL_RE.findall(text))


def last_run(event: object) -> LastRun | None:
    value = getattr(event, "_kaggle_last", None)
    return value if isinstance(value, LastRun) else None


def _paste_url(raw: str) -> str:
    """The paste service answers a duplicate upload with "File already exists:
    <url>" rather than a bare URL, so the URL has to be pulled back out."""
    match = _URL_RE.search(raw)
    return match.group(1) if match else raw


async def _share_artifact(item: kaggle_client.OutputFile) -> str:
    """Mirror one output file to the paste service and return its link."""
    blob = await run_in_executor(kaggle_client.fetch_file, item.url)
    ext = item.name.rsplit(".", 1)[-1] if "." in item.name else "txt"
    # Without raise_on_no_paste, web.paste returns its failure message as a
    # plain string, which would be reported as if it were a URL.
    url = await run_in_executor(
        partial(web.paste, blob, ext, raise_on_no_paste=True)
    )
    return _paste_url(url)


_LIVE_SAMPLE_S = 20


async def _live_text(token: str, ref: str) -> str:
    """What a still-running notebook has printed, so a wait reports something.

    Without this the only news from a run in flight is its state, which cannot
    separate a long download from a hang — and the run cannot be cancelled, so
    that distinction is the caller's only real decision.
    """
    log = await run_in_executor(
        kaggle_client.stream_log, token, ref, _LIVE_SAMPLE_S
    )
    if not log.strip():
        return (
            "It has printed nothing yet. Call kaggle_wait_for_notebook again — "
            "do not poll."
        )
    return (
        f"Output so far — use it to judge whether it is progressing or stuck, "
        f"then call kaggle_wait_for_notebook again:\n{log[-_LOG_TAIL_MAX:]}"
    )


async def _log_section(event: object, log: str) -> str:
    """A long log, reduced to the parts that explain it.

    A tail alone is close to useless on a big log: it is the end of the cascade,
    so it shows the last thing to break rather than the thing that broke. The
    first error is the cause, the tail is where it ended up, and the paste is
    everything in between for when neither is enough.
    """
    if len(log) <= _LOG_TAIL_MAX:
        return f"log:\n{log}"
    parts = []
    cause = kaggle_client.first_error(log)
    if cause:
        parts.append(
            "FIRST error — later ones are usually fallout from this one:\n"
            f"{cause}"
        )
    parts.append(
        f"log tail (last {_LOG_TAIL_MAX} of {len(log)} chars):\n{log[-_LOG_TAIL_MAX:]}"
    )
    url = await _paste_log(event, log)
    if url:
        parts.append(f"full log: {url} — web_fetch it to read the rest")
    return "\n".join(parts)


async def _paste_log(event: object, log: str) -> str:
    """A link to the whole log. Best-effort: it is a convenience, not the run."""
    try:
        url = await run_in_executor(
            partial(
                web.paste, log.encode("utf-8"), "txt", raise_on_no_paste=True
            )
        )
    except (web.NoPasteException, OSError, ValueError, RuntimeError):
        logger.warning("kaggle: log paste failed", exc_info=True)
        return ""
    link = _paste_url(url)
    _remember_urls(event, link)
    return link


async def _result_text(
    event: object, token: str, ref: str, state: str, timeout_s: int = 0
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
        # The model cannot infer the remedy from the state name, and this is the
        # one it reads — say what to do, not just what happened.
        parts.append(
            f"run was KILLED at its {timeout_s}s timeout before finishing, so "
            f"any artifacts below are partial. If the work genuinely needs "
            f"longer, re-run the SAME title with a bigger timeout_s; otherwise "
            f"make the code do less."
        )
    if files:
        parts.append(
            "files in /kaggle/working/ (share one with kaggle_notebook_output):"
            + "".join(f"\n- {f.name}" for f in files[:_ARTIFACT_LIST_MAX])
            + (
                f"\n… and {len(files) - _ARTIFACT_LIST_MAX} more"
                if len(files) > _ARTIFACT_LIST_MAX
                else ""
            )
        )
    if log:
        parts.append(await _log_section(event, log))
    return "\n".join(parts) or "(no output)"


@tool(
    name="kaggle_wait_for_notebook",
    description=(
        "BLOCK until a running notebook finishes, then return its outcome, files "
        "and log — the same result kaggle_run_notebook gives when it finishes in "
        "time.\n"
        "Use this whenever a run is still going. Do NOT sit in a loop calling "
        "kaggle_notebook_status: this waits for you in a single step, while "
        "polling burns a turn every few seconds and will run you out of turns "
        "before the notebook is done.\n"
        "If the run is still going when the wait elapses you get what it has "
        "PRINTED SO FAR — read it. That is how you tell a slow download from a "
        "hang, and how you catch a run doing the wrong thing early. Then call "
        "this again."
    ),
    schema={
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Notebook ref, 'owner/slug'.",
            },
            "wait_s": {
                "type": "integer",
                "description": "How long to wait, in seconds (default 300, max 600).",
            },
        },
        "required": ["ref"],
    },
    wrap_errors=True,
)
async def kaggle_wait_for_notebook(ctx, data) -> str:
    event = ctx.context
    ref = str(data.get("ref", "")).strip()
    if not ref:
        return "(error: ref required)"
    wait_s = max(5, min(int(data.get("wait_s") or 300), 600))
    try:
        token = kaggle_client.token_from_bot(event.bot)
    except kaggle_client.KaggleNotConfigured as e:
        return f"(error: {e})"
    state = await _poll(token, ref, wait_s)
    await _bookkeep(partial(_mark_status, ref, state))
    if state not in kaggle_client.TERMINAL_STATES:
        return f"{ref}: still {state} after waiting {wait_s}s.\n" + (
            await _live_text(token, ref)
        )
    _mark_done(ref)
    return f"{ref} finished ({state}).\n" + await _result_text(
        event, token, ref, state
    )


@tool(
    name="kaggle_notebook_status",
    description=(
        "One-shot check of a notebook run's state. If it is still running and you "
        "intend to wait for it, use kaggle_wait_for_notebook instead of calling "
        "this repeatedly. Terminal states: complete, error, cancelacknowledged "
        "(= killed by its timeout; partial artifacts still exist)."
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
        "List what a notebook run produced, plus the tail of its log. Pass "
        "`share=<path>` to publish ONE of those files and get a link for it — "
        "only share what the user asked for, not everything. Works even for runs "
        "killed by their timeout, which keep whatever they had already written."
    ),
    schema={
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Notebook ref, 'owner/slug'.",
            },
            "share": {
                "type": "string",
                "description": "Path of ONE file to upload and get a shareable link for, exactly as listed (e.g. 'song.wav'). Omit to just list what the run produced.",
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
    share = str(data.get("share", "")).strip()
    try:
        token = kaggle_client.token_from_bot(event.bot)
        files, log = await run_in_executor(kaggle_client.output, token, ref)
    except kaggle_client.KaggleError as e:
        return f"(error: {e})"

    if share:
        wanted = next((f for f in files if f.name == share), None)
        if wanted is None:
            names = ", ".join(f.name for f in files[:_ARTIFACT_LIST_MAX])
            return f"(error: no file named '{share}'. Available: {names or 'none'})"
        try:
            url = await _share_artifact(wanted)
        except (
            kaggle_client.KaggleError,
            OSError,
            ValueError,
            web.NoPasteException,
        ) as e:
            return f"(error sharing {share}: {e})"
        _remember_urls(event, url)
        return f"{share}: {url}"

    lines = []
    if not files:
        lines.append("no files (nothing written to /kaggle/working/)")
    else:
        lines.append(
            "files in /kaggle/working/ (pass one as `share` to publish it):"
        )
        lines.extend(f"- {item.name}" for item in files[:_ARTIFACT_LIST_MAX])
        if len(files) > _ARTIFACT_LIST_MAX:
            lines.append(f"… and {len(files) - _ARTIFACT_LIST_MAX} more")
    if log:
        lines.append(await _log_section(event, log))
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
    return format_notebooks(rows)


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
