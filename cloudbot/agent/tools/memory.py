"""Persistent key/value memory tools backed by the shared SQLite metadata.

The Table is declared at import time so SQLAlchemy registers it on the global
metadata object alongside other CloudBot tables — same pattern as the rest of
the codebase.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, String, Table, Text, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from cloudbot.agent.common import parse_namespace, run_in_executor
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


@tool(
    name="memory_set",
    description=(
        "Store a key-value pair in persistent memory. Use to remember facts, "
        "preferences, or notes across conversations. "
        "namespace defaults to the current channel. "
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
            "namespace": {
                "type": "string",
                "description": "Scope (default: current channel)",
            },
        },
        "required": ["key", "value"],
    },
)
async def memory_set(ctx, data):
    key = str(data.get("key") or "").strip()[:200]
    value = str(data.get("value") or "").strip()
    ns = parse_namespace(data, ctx)

    if not key:
        return "(error: key required)"
    if len(value) > _MEMORY_VALUE_MAX:
        return f"(error: value too long, max {_MEMORY_VALUE_MAX} chars)"

    def _do_upsert() -> None:
        db = database.Session()
        now = datetime.now(timezone.utc).isoformat()
        stmt = (
            sqlite_insert(_MEMORY_TABLE)
            .values(namespace=ns, key=key, value=value, updated_at=now)
            .on_conflict_do_update(
                index_elements=["namespace", "key"],
                set_={"value": value, "updated_at": now},
            )
        )
        try:
            db.execute(stmt)
            db.commit()
        except SQLAlchemyError as e:
            db.rollback()
            raise e

    try:
        await run_in_executor(_do_upsert)
    except SQLAlchemyError as e:
        return f"(error storing memory: {e})"
    return f"stored: {ns}/{key}"


@tool(
    name="memory_get",
    description=(
        "Retrieve a stored memory by key. "
        "namespace defaults to the current channel. "
        "Returns the value or a not-found message."
    ),
    schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key to retrieve"},
            "namespace": {
                "type": "string",
                "description": "Scope (default: current channel)",
            },
        },
        "required": ["key"],
    },
)
async def memory_get(ctx, data):
    key = str(data.get("key") or "").strip()
    ns = parse_namespace(data, ctx)

    if not key:
        return "(error: key required)"

    def _do_get() -> Any:
        db = database.Session()
        return db.execute(
            _MEMORY_TABLE.select().where(
                (_MEMORY_TABLE.c.namespace == ns) & (_MEMORY_TABLE.c.key == key)
            )
        ).first()

    try:
        row = await run_in_executor(_do_get)
    except SQLAlchemyError as e:
        return f"(error reading memory: {e})"
    if row is None:
        return f"(not found: {ns}/{key})"
    return f"{row['value']} [updated: {row['updated_at'][:16]}]"


@tool(
    name="memory_search",
    description=(
        "Search stored memories by keyword in key or value. "
        "namespace defaults to the current channel. "
        f"Returns up to {_MEMORY_SEARCH_LIMIT} matching entries."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword to search for (case-insensitive)",
            },
            "namespace": {
                "type": "string",
                "description": "Scope (default: current channel)",
            },
        },
        "required": ["query"],
    },
)
async def memory_search(ctx, data):
    query = str(data.get("query") or "").strip()
    ns = parse_namespace(data, ctx)

    if not query:
        return "(error: query required)"

    like = f"%{query}%"

    def _do_search() -> list[Any]:
        db = database.Session()
        rows = db.execute(
            _MEMORY_TABLE.select()
            .where(
                (_MEMORY_TABLE.c.namespace == ns)
                & or_(
                    _MEMORY_TABLE.c.key.ilike(like),
                    _MEMORY_TABLE.c.value.ilike(like),
                )
            )
            .order_by(_MEMORY_TABLE.c.updated_at.desc())
            .limit(_MEMORY_SEARCH_LIMIT)
        ).fetchall()
        return list(rows)

    try:
        matches = await run_in_executor(_do_search)
    except SQLAlchemyError as e:
        return f"(error searching memory: {e})"
    if not matches:
        return f"(no memories found for '{query}' in {ns})"
    lines = [f"{r['key']}: {(r['value'] or '')[:200]}" for r in matches]
    return "\n".join(lines)
