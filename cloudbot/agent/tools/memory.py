"""Persistent key/value memory tools backed by the shared SQLite metadata.

The Table is declared at import time so SQLAlchemy registers it on the global
metadata object alongside other CloudBot tables — same pattern as the rest of
the codebase.
"""

import re
import unicodedata
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
# agent_memory's rowid; triggers keep it synced. Both the memory_search tool and
# the agent's recall of memories matching a request read it.
_FTS_TABLE = "agent_memory_fts"
_FTS_VOCAB = "agent_memory_fts_vocab"
_FTS_TOKEN_RE = re.compile(r"[^\W_]+")
_SEARCH_TERMS_MAX = 20
_RECALL_TERMS_MAX = 60
# Recall drops words found in more than this share of memories ("the", "de",
# "is" in any language), once there are enough memories for the share to mean
# something, and keeps only matches scoring at least half as well as the best.
_COMMON_TERM_SHARE = 0.15
_COMMON_TERM_MIN_MEMORIES = 20
_RECALL_SCORE_SHARE = 0.5

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
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_VOCAB} USING fts5vocab({_FTS_TABLE}, 'row')",
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


def _fold(text_: str) -> str:
    """Lowercase and strip accents, the way the unicode61 tokenizer indexes words.

    The vocabulary table holds folded words, so we fold the query to compare.
    """
    decomposed = unicodedata.normalize("NFKD", text_.lower())
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )


def _query_terms(raw: str, limit: int) -> list[str]:
    """The distinct words of free-form text, in order, as FTS5 search terms."""
    terms = [tok for tok in _FTS_TOKEN_RE.findall(_fold(raw)) if len(tok) >= 2]
    return list(dict.fromkeys(terms))[:limit]


def _match_expression(terms: Sequence[str]) -> str:
    """OR the terms, each quoted so FTS5 operators in user text stay words."""
    return " OR ".join(f'"{term}"' for term in terms)


def _ranked_matches(
    db: Any, namespaces: Sequence[str], terms: Sequence[str], limit: int
) -> list[tuple[str, str, float]]:
    sql = text(
        f"SELECT key, value, bm25({_FTS_TABLE}) FROM {_FTS_TABLE} "
        f"WHERE {_FTS_TABLE} MATCH :q AND namespace IN :ns "
        f"ORDER BY bm25({_FTS_TABLE}) LIMIT :lim"
    ).bindparams(bindparam("ns", expanding=True))
    rows = db.execute(
        sql,
        {"q": _match_expression(terms), "ns": list(namespaces), "lim": limit},
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def fts_search(
    namespaces: Sequence[str], query: str, limit: int
) -> list[tuple[str, str]]:
    """bm25-ranked keyword search over stored memories across namespaces."""
    terms = _query_terms(query, _SEARCH_TERMS_MAX)
    if not terms or not namespaces:
        return []
    matches = _ranked_matches(database.Session(), namespaces, terms, limit)
    return [(key, value) for key, value, _score in matches]


def _distinctive_terms(db: Any, terms: list[str]) -> list[str]:
    memories = (
        db.execute(text("SELECT count(*) FROM agent_memory")).scalar() or 0
    )
    if memories < _COMMON_TERM_MIN_MEMORIES:
        return terms
    sql = text(
        f"SELECT term FROM {_FTS_VOCAB} WHERE term IN :terms AND doc > :cutoff"
    ).bindparams(bindparam("terms", expanding=True))
    common = {
        row[0]
        for row in db.execute(
            sql, {"terms": terms, "cutoff": memories * _COMMON_TERM_SHARE}
        )
    }
    return [term for term in terms if term not in common]


def recall_memories(
    namespaces: Sequence[str], context: str, limit: int
) -> list[tuple[str, str]]:
    """The memories that bear on this text, best first.

    We search on the words that set a memory apart and keep only matches that
    score at least half as well as the best one, so a request made of everyday
    words brings back nothing at all.
    """
    terms = _query_terms(context, _RECALL_TERMS_MAX)
    if not terms or not namespaces:
        return []
    db = database.Session()
    terms = _distinctive_terms(db, terms)
    if not terms:
        return []
    matches = _ranked_matches(db, namespaces, terms, limit)
    if not matches:
        return []
    cutoff = matches[0][2] * _RECALL_SCORE_SHARE
    return [(key, value) for key, value, score in matches if score <= cutoff]


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
