"""
ratelimit.py

Shared persistent rate-limit table. One row per recorded call; each row
carries a `weight` so the same table can back request-count limits
(weight=1) and char-count limits (weight=len(text)).

Buckets are global strings — same bucket = one counter across all
channels, networks, and bot restarts. Plugin loader auto-creates the
table on first load (`obj.metadata == database.metadata` convention).

Usage:
    from plugins.ratelimit import Limit, check, record

    LIMITS = [
        Limit(60, 8, "Rate limited. Try again in a minute."),
        Limit(86400, 450, "Daily limit reached."),
    ]

    def my_hook(text, db):
        msg = check(db, "my-bucket", LIMITS)
        if msg:
            return msg
        # ... call external API ...
        record(db, "my-bucket")
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    func,
    select,
)

from cloudbot.util import database

ratelimit_table = Table(
    "ratelimit_events",
    database.metadata,
    Column("bucket", String, nullable=False),
    Column("ts", DateTime, nullable=False),
    Column("weight", Integer, nullable=False, server_default="1"),
)

Index(
    "ix_ratelimit_bucket_ts",
    ratelimit_table.c.bucket,
    ratelimit_table.c.ts,
)


@dataclass(frozen=True)
class Limit:
    seconds: int
    max_weight: int
    message: str


def check(db, bucket: str, limits: Iterable[Limit]) -> str | None:
    """Prune expired rows for this bucket and verify all limits.

    Returns the message of the first exceeded limit, or None if all pass.
    Does NOT insert anything — call `record` after a successful operation
    so failed external calls don't burn quota.
    """
    limit_list = list(limits)
    if not limit_list:
        return None

    now = datetime.utcnow()
    longest = max(lim.seconds for lim in limit_list)
    db.execute(
        ratelimit_table.delete().where(
            (ratelimit_table.c.bucket == bucket)
            & (ratelimit_table.c.ts < now - timedelta(seconds=longest))
        )
    )

    for lim in sorted(limit_list, key=lambda x: x.seconds):
        cutoff = now - timedelta(seconds=lim.seconds)
        total = (
            db.execute(
                select(func.coalesce(func.sum(ratelimit_table.c.weight), 0))
                .where(ratelimit_table.c.bucket == bucket)
                .where(ratelimit_table.c.ts >= cutoff)
            ).scalar()
            or 0
        )
        if total >= lim.max_weight:
            return lim.message
    return None


def record(db, bucket: str, weight: int = 1) -> None:
    """Append a successful event to the bucket and commit."""
    db.execute(
        ratelimit_table.insert().values(
            bucket=bucket, ts=datetime.utcnow(), weight=weight
        )
    )
    db.commit()
