from datetime import datetime, timedelta

import pytest

from plugins.ratelimit import Limit, check, ratelimit_table, record


@pytest.fixture()
def setup_db(mock_db):
    ratelimit_table.create(mock_db.engine)


@pytest.fixture()
def db(mock_db, setup_db):
    return mock_db.session()


def _seed(db, bucket: str, ago_seconds: int, weight: int = 1) -> None:
    db.execute(
        ratelimit_table.insert().values(
            bucket=bucket,
            ts=datetime.utcnow() - timedelta(seconds=ago_seconds),
            weight=weight,
        )
    )
    db.commit()


def test_check_empty_returns_none(db):
    assert check(db, "x", [Limit(60, 5, "nope")]) is None


def test_check_no_limits_returns_none(db):
    record(db, "x")
    assert check(db, "x", []) is None


def test_record_then_check_under_limit(db):
    limits = [Limit(60, 3, "minute cap")]
    for _ in range(2):
        record(db, "books")
    assert check(db, "books", limits) is None


def test_check_blocks_at_threshold(db):
    limits = [Limit(60, 2, "minute cap")]
    record(db, "books")
    record(db, "books")
    assert check(db, "books", limits) == "minute cap"


def test_check_uses_weights(db):
    limits = [Limit(86400, 100, "char cap")]
    record(db, "tx", weight=60)
    assert check(db, "tx", limits) is None
    record(db, "tx", weight=40)
    assert check(db, "tx", limits) == "char cap"


def test_check_buckets_isolated(db):
    limits = [Limit(60, 1, "cap")]
    record(db, "a")
    assert check(db, "a", limits) == "cap"
    assert check(db, "b", limits) is None


def test_check_returns_shortest_failing_window(db):
    limits = [
        Limit(60, 100, "minute cap"),
        Limit(86400, 2, "daily cap"),
    ]
    record(db, "x")
    record(db, "x")
    assert check(db, "x", limits) == "daily cap"


def test_old_rows_pruned_on_check(db):
    limits = [Limit(60, 2, "minute cap")]
    _seed(db, "x", ago_seconds=120)
    _seed(db, "x", ago_seconds=120)
    _seed(db, "x", ago_seconds=120)
    # All three rows are outside the 60s window AND outside the longest
    # declared window (60s). check() should prune them.
    assert check(db, "x", limits) is None
    remaining = db.execute(ratelimit_table.select()).fetchall()
    assert remaining == []


def test_rows_outside_short_window_dont_block(db):
    limits = [
        Limit(60, 1, "minute"),
        Limit(86400, 10, "day"),
    ]
    _seed(db, "x", ago_seconds=120)  # outside 60s, inside 24h
    assert check(db, "x", limits) is None


def test_rows_outside_longest_window_pruned(db):
    limits = [Limit(3600, 1000, "hour")]
    _seed(db, "x", ago_seconds=7200)
    assert check(db, "x", limits) is None
    assert db.execute(ratelimit_table.select()).fetchall() == []


def test_record_after_check_pattern(db):
    """Failed external calls don't burn quota when caller follows check-then-record-on-success."""
    limits = [Limit(60, 2, "cap")]
    assert check(db, "x", limits) is None
    # caller would attempt external API; on failure, no record() call.
    # On success, record:
    record(db, "x")
    assert check(db, "x", limits) is None
    record(db, "x")
    assert check(db, "x", limits) == "cap"
