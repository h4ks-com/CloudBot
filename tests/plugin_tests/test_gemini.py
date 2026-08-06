"""Integration tests for plugins/gemini.py rate-limit wiring.

API calls are mocked via `responses`. Database is per-test in-memory sqlite.
Time is controlled with freezegun to verify window expiry behavior.
"""

import datetime
from datetime import timedelta

import pytest

from plugins import gemini, ratelimit

# ---------- fixtures ----------


@pytest.fixture()
def setup_db(mock_db):
    ratelimit.ratelimit_table.create(mock_db.engine)


@pytest.fixture()
def db(mock_db, setup_db):
    return mock_db.session()


@pytest.fixture(autouse=True)
def clear_gemt_cache():
    gemini.gemt_messages_cache.clear()
    yield
    gemini.gemt_messages_cache.clear()


def _set_key(mock_bot, key: str = "fakekey") -> None:
    mock_bot.config["api_keys"] = {"gemini": key, "google": key}


def _text_url() -> str:
    return gemini.GEMINI_BASE + gemini.GEMINI_TEXT_MODEL + ":generateContent"


def _mock_text_ok(mock_requests, text: str = "hello world"):
    mock_requests.add(
        "POST",
        _text_url(),
        json={"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


def _bucket_count(db, bucket: str) -> int:
    return len(
        db.execute(
            ratelimit.ratelimit_table.select().where(
                ratelimit.ratelimit_table.c.bucket == bucket
            )
        ).fetchall()
    )


# ---------- .gemt: input validation ----------


def test_gemt_missing_key(mock_bot, db):
    mock_bot.config["api_keys"] = {}
    res = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "Gemini API key not configured" in res


def test_gemt_empty_prompt(mock_bot, db):
    _set_key(mock_bot)
    res = gemini.gemt_command("   ", "nick", "#chan", db)
    assert res == "Usage: .gemt <text>"


# ---------- .gemt: happy path + recording ----------


def test_gemt_success_returns_text(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    _mock_text_ok(mock_requests, "ack")
    res = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "ack" in res


def test_gemt_records_event_on_success(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    _mock_text_ok(mock_requests)
    assert _bucket_count(db, gemini.TEXT_BUCKET) == 0
    gemini.gemt_command("hi", "nick", "#chan", db)
    assert _bucket_count(db, gemini.TEXT_BUCKET) == 1


# ---------- .gemt: failed HTTP must not burn quota ----------


def test_gemt_http_500_does_not_record(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    mock_requests.add("POST", _text_url(), status=500)
    res = gemini.gemt_command("hi", "nick", "#chan", db)
    assert res.startswith("Gemini API error")
    assert _bucket_count(db, gemini.TEXT_BUCKET) == 0


def test_gemt_http_429_does_not_record(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    mock_requests.add("POST", _text_url(), status=429)
    res = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "API error" in res or "Request failed" in res
    assert _bucket_count(db, gemini.TEXT_BUCKET) == 0


def test_gemt_empty_candidates_does_not_record(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    mock_requests.add("POST", _text_url(), json={"candidates": []})
    gemini.gemt_command("hi", "nick", "#chan", db)
    assert _bucket_count(db, gemini.TEXT_BUCKET) == 0


def test_gemt_blocked_prompt_does_not_record(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    mock_requests.add(
        "POST",
        _text_url(),
        json={
            "candidates": [],
            "promptFeedback": {"blockReason": "SAFETY"},
        },
    )
    res = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "blocked: SAFETY" in res
    assert _bucket_count(db, gemini.TEXT_BUCKET) == 0


# ---------- .gemt: window enforcement ----------


def test_gemt_rpm_cap_blocks_next_call(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    for _ in range(gemini.TEXT_MAX_RPM):
        mock_requests.add(
            "POST",
            _text_url(),
            json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )
    for _ in range(gemini.TEXT_MAX_RPM):
        out = gemini.gemt_command("hi", "nick", "#chan", db)
        assert "ok" in out, out
    res = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "Rate limited" in res
    assert _bucket_count(db, gemini.TEXT_BUCKET) == gemini.TEXT_MAX_RPM


def test_gemt_rpd_cap_blocks_after_seeding(mock_bot, db):
    _set_key(mock_bot)
    # Seed RPD-many rows OUTSIDE the 1-minute window (so RPM doesn't trip)
    # but INSIDE the 24h window (so RPD does).
    now = datetime.datetime.utcnow()
    for i in range(gemini.TEXT_MAX_RPD):
        db.execute(
            ratelimit.ratelimit_table.insert().values(
                bucket=gemini.TEXT_BUCKET,
                ts=now - timedelta(seconds=61) - timedelta(seconds=i),
                weight=1,
            )
        )
    db.commit()
    res = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "Daily" in res
    # API was NOT called → no extra row recorded
    assert _bucket_count(db, gemini.TEXT_BUCKET) == gemini.TEXT_MAX_RPD


def test_gemt_rpm_resets_after_window(mock_bot, mock_requests, db, freeze_time):
    _set_key(mock_bot)
    for _ in range(gemini.TEXT_MAX_RPM + 1):
        mock_requests.add(
            "POST",
            _text_url(),
            json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )
    for _ in range(gemini.TEXT_MAX_RPM):
        gemini.gemt_command("hi", "nick", "#chan", db)
    assert "Rate limited" in gemini.gemt_command("hi", "nick", "#chan", db)
    freeze_time.tick(delta=timedelta(seconds=61))
    out = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "ok" in out, out


def test_gemt_rpd_resets_after_24h(mock_bot, mock_requests, db, freeze_time):
    _set_key(mock_bot)
    # Seed at RPD cap outside the minute window so RPM is dormant but RPD is at cap.
    now = datetime.datetime.utcnow()
    for i in range(gemini.TEXT_MAX_RPD):
        db.execute(
            ratelimit.ratelimit_table.insert().values(
                bucket=gemini.TEXT_BUCKET,
                ts=now - timedelta(seconds=61) - timedelta(seconds=i),
                weight=1,
            )
        )
    db.commit()
    assert "Daily" in gemini.gemt_command("hi", "nick", "#chan", db)
    freeze_time.tick(delta=timedelta(hours=24, seconds=1))
    _mock_text_ok(mock_requests, "fresh")
    out = gemini.gemt_command("hi", "nick", "#chan", db)
    assert "fresh" in out


# ---------- bucket isolation: text usage doesn't cap image ----------


def test_text_usage_does_not_consume_image_quota(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    for _ in range(gemini.TEXT_MAX_RPM):
        mock_requests.add(
            "POST",
            _text_url(),
            json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )
    for _ in range(gemini.TEXT_MAX_RPM):
        gemini.gemt_command("hi", "nick", "#chan", db)
    assert _bucket_count(db, gemini.IMG_BUCKET) == 0
    assert _bucket_count(db, gemini.TEXT_BUCKET) == gemini.TEXT_MAX_RPM


# ---------- .gemimg: rate-limit smoke ----------


def test_gemimg_no_api_key(mock_bot, db):
    mock_bot.config["api_keys"] = {}
    res = gemini.gemi_command("draw a cat", "#chan", "nick", db)
    assert "Gemini API key not configured" in res


def test_gemimg_rpm_cap_blocks_next_call(mock_bot, db):
    """Without mocking the upload pipeline we can't reach the success path,
    but the rate-limit check fires BEFORE any HTTP, so pre-seed and verify.
    """
    _set_key(mock_bot)
    now = datetime.datetime.utcnow()
    for _ in range(gemini.IMG_MAX_RPM):
        db.execute(
            ratelimit.ratelimit_table.insert().values(
                bucket=gemini.IMG_BUCKET, ts=now, weight=1
            )
        )
    db.commit()
    res = gemini.gemi_command("a prompt", "#chan", "nick", db)
    assert "Rate limited" in res


# ---------- conversation history bookkeeping ----------


def test_gemt_history_appended_on_success(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    _mock_text_ok(mock_requests, "world")
    gemini.gemt_command("hello", "nick", "#chan", db)
    hist = gemini.gemt_messages_cache[("#chan", "nick")]
    assert [m.role for m in hist] == ["user", "assistant"]
    assert hist[0].content == "hello"
    assert hist[1].content == "world"


def test_gemt_history_pops_user_msg_on_http_error(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    mock_requests.add("POST", _text_url(), status=500)
    gemini.gemt_command("doomed", "nick", "#chan", db)
    assert ("#chan", "nick") not in gemini.gemt_messages_cache or len(
        gemini.gemt_messages_cache[("#chan", "nick")]
    ) == 0


def test_gemtclear_removes_history(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    _mock_text_ok(mock_requests)
    gemini.gemt_command("hi", "nick", "#chan", db)
    assert ("#chan", "nick") in gemini.gemt_messages_cache
    msg = gemini.gemtclear_command("nick", "#chan")
    assert "cleared" in msg.lower()
    assert ("#chan", "nick") not in gemini.gemt_messages_cache


# ---------- aliases ----------


def test_gemt_aliases_registered():
    aliases = gemini.gemt_command._cloudbot_hook["command"].aliases
    assert {"gemt", "gai", "gae"}.issubset(aliases)
