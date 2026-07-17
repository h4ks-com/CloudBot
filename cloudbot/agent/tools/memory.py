"""Persistent key/value memory tools backed by the shared SQLite metadata.

The Table is declared at import time so SQLAlchemy registers it on the global
metadata object alongside other CloudBot tables — same pattern as the rest of
the codebase.
"""

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, String, Table, Text, bindparam, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from cloudbot.agent.common import (
    memory_namespace,
    memory_read_namespaces,
    parse_scope,
    run_in_executor,
)
from cloudbot.agent.registry import tool
from cloudbot.util import database

_MEMORY_TABLE = Table(
    "agent_memory",
    database.metadata,
    Column("namespace", String(100), primary_key=True),
    Column("key", String(200), primary_key=True),
    Column("value", Text),
    Column("updated_at", String(32)),
    extend_existing=True,
)

_MEMORY_VALUE_MAX = 2000
_MEMORY_SEARCH_LIMIT = 20

_SCOPE_PROPERTY = {
    "type": "string",
    "enum": ["user", "channel", "network"],
    "description": (
        "Who the memory is about. 'user': the person you are talking to, on "
        "this network. 'channel': this channel, shared by everyone in it. "
        "'network': everyone on this network. Memories never cross networks."
    ),
}


def ensure_memory_table(engine: Engine) -> None:
    """Create the agent_memory table if absent (idempotent, for fresh DBs)."""
    _MEMORY_TABLE.create(bind=engine, checkfirst=True)


def all_memories(
    namespaces: Sequence[str], limit: int
) -> list[tuple[str, str]]:
    """Every memory across these namespaces, newest first (capped at limit)."""
    if not namespaces:
        return []
    db = database.Session()
    rows = db.execute(
        _MEMORY_TABLE.select()
        .with_only_columns(_MEMORY_TABLE.c.key, _MEMORY_TABLE.c.value)
        .where(_MEMORY_TABLE.c.namespace.in_(namespaces))
        .order_by(_MEMORY_TABLE.c.updated_at.desc())
        .limit(limit)
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def store_memory(namespace: str, key: str, value: str) -> None:
    """Upsert one memory (insert, or overwrite by namespace+key)."""
    db = database.Session()
    now = datetime.now(timezone.utc).isoformat()
    stmt = (
        sqlite_insert(_MEMORY_TABLE)
        .values(namespace=namespace, key=key, value=value, updated_at=now)
        .on_conflict_do_update(
            index_elements=["namespace", "key"],
            set_={"value": value, "updated_at": now},
        )
    )
    try:
        db.execute(stmt)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


# FTS5 mirror powering the memory_search tool: bm25-ranked, word-boundary
# matching via the unicode61 tokenizer (language-neutral — no English stemmer,
# diacritics folded consistently on both index and query). External content off
# agent_memory's rowid; triggers keep it synced. Recall (the prompt index) does
# NOT use this — only the agent's explicit memory_search does.
_FTS_TABLE = "agent_memory_fts"
_FTS_TOKEN_RE = re.compile(r"[^\W_]+")

_FTS_DDL = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5("
    "namespace UNINDEXED, key, value, "
    "content='agent_memory', content_rowid='rowid', tokenize='unicode61')",
    "CREATE TRIGGER IF NOT EXISTS agent_memory_ai AFTER INSERT ON agent_memory "
    f"BEGIN INSERT INTO {_FTS_TABLE}(rowid, namespace, key, value) "
    "VALUES (new.rowid, new.namespace, new.key, new.value); END",
    "CREATE TRIGGER IF NOT EXISTS agent_memory_ad AFTER DELETE ON agent_memory "
    f"BEGIN INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, namespace, key, value) "
    "VALUES ('delete', old.rowid, old.namespace, old.key, old.value); END",
    "CREATE TRIGGER IF NOT EXISTS agent_memory_au AFTER UPDATE ON agent_memory "
    f"BEGIN INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, namespace, key, value) "
    "VALUES ('delete', old.rowid, old.namespace, old.key, old.value); "
    f"INSERT INTO {_FTS_TABLE}(rowid, namespace, key, value) "
    "VALUES (new.rowid, new.namespace, new.key, new.value); END",
)


def ensure_fts(engine: Engine) -> None:
    """Create the base table + FTS5 mirror & sync triggers (idempotent).

    Rebuilt at startup so the index always matches the base table even if a
    write ever bypassed the triggers.
    """
    ensure_memory_table(engine)
    with engine.begin() as cx:
        for stmt in _FTS_DDL:
            cx.exec_driver_sql(stmt)
        cx.exec_driver_sql(
            f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}) VALUES ('rebuild')"
        )


def _fts_match_query(raw: str) -> str | None:
    """Build a safe FTS5 MATCH expression (OR of quoted terms) from user text.

    Quoting each term neutralises FTS5 operators in free-form input; the
    unicode61 tokenizer keeps it language-agnostic. No stopword list.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for tok in _FTS_TOKEN_RE.findall(raw.lower()):
        if len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        terms.append(tok)
        if len(terms) >= 20:
            break
    if not terms:
        return None
    return " OR ".join(f'"{tok}"' for tok in terms)


def fts_search(
    namespaces: Sequence[str], query: str, limit: int
) -> list[tuple[str, str]]:
    """bm25-ranked keyword search over stored memories across namespaces."""
    match = _fts_match_query(query)
    if not match or not namespaces:
        return []
    db = database.Session()
    sql = text(
        f"SELECT key, value FROM {_FTS_TABLE} "
        f"WHERE {_FTS_TABLE} MATCH :q AND namespace IN :ns "
        f"ORDER BY bm25({_FTS_TABLE}) LIMIT :lim"
    ).bindparams(bindparam("ns", expanding=True))
    rows = db.execute(
        sql, {"q": match, "ns": list(namespaces), "lim": limit}
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


@tool(
    name="memory_set",
    description=(
        "Store a key-value pair in persistent memory. Use to remember facts, "
        "preferences, or notes across conversations.\n"
        "Choose the scope by who the fact is ABOUT, not who mentioned it: a "
        "person's own preference is 'user' even when said in a channel, while "
        "something true of the whole channel is 'channel'. Getting this right is "
        "what decides who you can recall it for later. Defaults to 'user'.\n"
        f"Value is capped at {_MEMORY_VALUE_MAX} chars."
    ),
    schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (max 200 chars)",
            },
            "value": {
                "type": "string",
                "description": f"Value to store (max {_MEMORY_VALUE_MAX} chars)",
            },
            "scope": _SCOPE_PROPERTY,
        },
        "required": ["key", "value"],
    },
)
async def memory_set(ctx, data):
    key = str(data.get("key") or "").strip()[:200]
    value = str(data.get("value") or "").strip()
    scope = parse_scope(data) or "user"
    ns = memory_namespace(ctx.context, scope)

    if not key:
        return "(error: key required)"
    if not ns:
        return f"(error: nothing here to scope a '{scope}' memory to)"
    if len(value) > _MEMORY_VALUE_MAX:
        return f"(error: value too long, max {_MEMORY_VALUE_MAX} chars)"

    def _do_upsert() -> None:
        store_memory(ns, key, value)

    try:
        await run_in_executor(_do_upsert)
    except SQLAlchemyError as e:
        return f"(error storing memory: {e})"
    return f"stored: {ns}/{key}"


@tool(
    name="memory_get",
    description=(
        "Retrieve a stored memory by key. Searches everything this channel and "
        "this user can see unless you name a scope. Returns the value or a "
        "not-found message."
    ),
    schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key to retrieve"},
            "scope": _SCOPE_PROPERTY,
        },
        "required": ["key"],
    },
)
async def memory_get(ctx, data):
    key = str(data.get("key") or "").strip()
    namespaces = memory_read_namespaces(ctx.context, parse_scope(data))

    if not key:
        return "(error: key required)"
    if not namespaces:
        return "(error: nothing here to read a memory from)"

    def _do_get() -> Any:
        db = database.Session()
        return db.execute(
            _MEMORY_TABLE.select()
            .where(
                _MEMORY_TABLE.c.namespace.in_(namespaces)
                & (_MEMORY_TABLE.c.key == key)
            )
            .order_by(_MEMORY_TABLE.c.updated_at.desc())
        ).first()

    try:
        row = await run_in_executor(_do_get)
    except SQLAlchemyError as e:
        return f"(error reading memory: {e})"
    if row is None:
        return f"(not found: {key})"
    return (
        f"{row['value']} [about: {row['namespace']}, "
        f"updated: {row['updated_at'][:16]}]"
    )


@tool(
    name="memory_search",
    description=(
        "Search stored memories by keyword in key or value. Searches everything "
        "this channel and this user can see unless you name a scope. "
        f"Returns up to {_MEMORY_SEARCH_LIMIT} matching entries."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword to search for (case-insensitive)",
            },
            "scope": _SCOPE_PROPERTY,
        },
        "required": ["query"],
    },
)
async def memory_search(ctx, data):
    query = str(data.get("query") or "").strip()
    namespaces = memory_read_namespaces(ctx.context, parse_scope(data))

    if not query:
        return "(error: query required)"
    if not namespaces:
        return "(error: nothing here to search memories in)"

    def _do_search() -> list[tuple[str, str]]:
        return fts_search(namespaces, query, _MEMORY_SEARCH_LIMIT)

    try:
        matches = await run_in_executor(_do_search)
    except SQLAlchemyError as e:
        return f"(error searching memory: {e})"
    if not matches:
        return f"(no memories found for '{query}')"
    lines = [f"{key}: {(value or '')[:200]}" for key, value in matches]
    return "\n".join(lines)
