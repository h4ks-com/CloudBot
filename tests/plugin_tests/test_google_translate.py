"""Integration tests for plugins/google_translate.py rate-limit wiring.

The translate plugin uses *char-weighted* limits — each row records
`len(text)` instead of 1. These tests verify both the wiring and the
weighted accumulation across calls.
"""

import datetime
from datetime import timedelta

import pytest

from plugins import google_translate, ratelimit

TRANSLATE_URL = "https://www.googleapis.com/language/translate/v2"


@pytest.fixture()
def setup_db(mock_db):
    ratelimit.ratelimit_table.create(mock_db.engine)


@pytest.fixture()
def db(mock_db, setup_db):
    return mock_db.session()


def _set_key(mock_bot, key: str = "fakekey") -> None:
    mock_bot.config["api_keys"] = {"google": key}


def _mock_ok(mock_requests, translated: str = "hola"):
    mock_requests.add(
        "GET",
        TRANSLATE_URL,
        json={
            "data": {
                "translations": [
                    {
                        "translatedText": translated,
                        "detectedSourceLanguage": "en",
                    }
                ]
            }
        },
    )


def _bucket_total(db) -> int:
    rows = db.execute(
        ratelimit.ratelimit_table.select().where(
            ratelimit.ratelimit_table.c.bucket
            == google_translate.TRANSLATE_BUCKET
        )
    ).fetchall()
    return sum(r.weight for r in rows)


# ---------- validation ----------


def test_no_api_key(mock_bot, db):
    mock_bot.config["api_keys"] = {}
    assert (
        google_translate.translate("hello world", db)
        == "This command requires a Google API key."
    )


def test_text_too_long(mock_bot, db):
    _set_key(mock_bot)
    res = google_translate.translate("x" * 200, db)
    assert "only supports input of less" in res


# ---------- happy path + weighted recording ----------


def test_translate_success_records_char_weight(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    _mock_ok(mock_requests, "hola")
    text = "hello world"  # default target=en, source autodetect
    res = google_translate.translate(text, db)
    assert "hola" in res
    assert _bucket_total(db) == len(text)


def test_two_calls_accumulate(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    _mock_ok(mock_requests, "x")
    _mock_ok(mock_requests, "y")
    google_translate.translate("abc", db)
    google_translate.translate("defgh", db)
    assert _bucket_total(db) == 8


# ---------- failed HTTP must not burn quota ----------


def test_api_error_does_not_record(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    mock_requests.add(
        "GET",
        TRANSLATE_URL,
        json={"error": {"code": 403}},
    )
    res = google_translate.translate("hi", db)
    assert "Translate API is off" in res
    assert _bucket_total(db) == 0


def test_generic_api_error_does_not_record(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    mock_requests.add(
        "GET", TRANSLATE_URL, json={"error": {"code": 500}}
    )
    res = google_translate.translate("hi", db)
    assert res == "Google API error."
    assert _bucket_total(db) == 0


# ---------- daily char cap ----------


def test_char_cap_blocks_when_seeded_above_threshold(mock_bot, db):
    _set_key(mock_bot)
    # Seed a single row weighted at the cap.
    db.execute(
        ratelimit.ratelimit_table.insert().values(
            bucket=google_translate.TRANSLATE_BUCKET,
            ts=datetime.datetime.utcnow(),
            weight=google_translate.TRANSLATE_MAX_CHARS_PER_DAY,
        )
    )
    db.commit()
    res = google_translate.translate("nope", db)
    assert "Daily Translate cap reached" in res
    # No HTTP fired → weight unchanged.
    assert _bucket_total(db) == google_translate.TRANSLATE_MAX_CHARS_PER_DAY


def test_char_cap_does_not_block_when_under(mock_bot, mock_requests, db):
    _set_key(mock_bot)
    # Pre-seed with cap-100, leaving 100 chars of headroom.
    db.execute(
        ratelimit.ratelimit_table.insert().values(
            bucket=google_translate.TRANSLATE_BUCKET,
            ts=datetime.datetime.utcnow(),
            weight=google_translate.TRANSLATE_MAX_CHARS_PER_DAY - 100,
        )
    )
    db.commit()
    _mock_ok(mock_requests, "ok")
    res = google_translate.translate("under cap", db)
    assert "ok" in res


def test_char_cap_resets_after_24h(mock_bot, mock_requests, db, freeze_time):
    _set_key(mock_bot)
    db.execute(
        ratelimit.ratelimit_table.insert().values(
            bucket=google_translate.TRANSLATE_BUCKET,
            ts=datetime.datetime.utcnow(),
            weight=google_translate.TRANSLATE_MAX_CHARS_PER_DAY,
        )
    )
    db.commit()
    # capped now
    assert "Daily" in google_translate.translate("blocked", db)
    # advance past 24h
    freeze_time.tick(delta=timedelta(hours=24, seconds=1))
    _mock_ok(mock_requests, "fresh")
    res = google_translate.translate("after", db)
    assert "fresh" in res
