import datetime
from datetime import timedelta
from unittest.mock import MagicMock, call

import pytest
import requests

from plugins import books, ratelimit


@pytest.fixture()
def setup_db(mock_db):
    ratelimit.ratelimit_table.create(mock_db.engine)


@pytest.fixture()
def db(mock_db, setup_db):
    return mock_db.session()


def _bucket_count(db) -> int:
    return len(
        db.execute(
            ratelimit.ratelimit_table.select().where(
                ratelimit.ratelimit_table.c.bucket == books.BOOKS_BUCKET
            )
        ).fetchall()
    )


def test_no_key(mock_bot, mock_requests, db):
    event = MagicMock()
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == "This command requires a Google API key."
    assert event.mock_calls == []


def test_books_no_results(mock_bot, mock_requests, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={"totalItems": 0},
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == "No results found."
    assert event.mock_calls == []


def test_books_error_code(mock_bot, mock_requests, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        status=404,
    )
    with pytest.raises(requests.HTTPError):
        books.books("foo", event.reply, mock_bot, db)

    assert event.mock_calls == [call.reply("API error occurred.")]


def test_books_error(mock_bot, mock_requests, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={"error": {"code": 404}},
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == "Error performing search."
    assert event.mock_calls == []


def test_books_error_api_off(mock_bot, mock_requests, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={"error": {"code": 403}},
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert (
        res
        == "The Books API is off in the Google Developers Console (or check the console)."
    )
    assert event.mock_calls == []


def test_books(mock_bot, mock_requests, patch_try_shorten, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [
                {
                    "volumeInfo": {
                        "title": "foo",
                        "infoLink": "foo.bar",
                        "authors": ["foo", "bar"],
                        "publisher": "test publisher",
                        "description": "foobar",
                        "publishedDate": "2020-07-05",
                        "pageCount": 5,
                    }
                }
            ],
        },
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == (
        "\x02foo\x02 by \x02foo\x02 (2020) - 5 pages - foobar - foo.bar"
    )
    assert event.mock_calls == []


def test_books_no_authors(mock_bot, mock_requests, patch_try_shorten, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [
                {
                    "volumeInfo": {
                        "title": "foo",
                        "infoLink": "foo.bar",
                        "publisher": "test publisher",
                        "description": "foobar",
                        "publishedDate": "2020-07-05",
                        "pageCount": 5,
                    }
                }
            ],
        },
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == (
        "\x02foo\x02 by \x02test publisher\x02 (2020) - 5 pages - foobar - foo.bar"
    )
    assert event.mock_calls == []


def test_books_no_desc(mock_bot, mock_requests, patch_try_shorten, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [
                {
                    "volumeInfo": {
                        "title": "foo",
                        "infoLink": "foo.bar",
                        "authors": ["foo", "bar"],
                        "publisher": "test publisher",
                        "publishedDate": "2020-07-05",
                        "pageCount": 5,
                    }
                }
            ],
        },
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == (
        "\x02foo\x02 by \x02foo\x02 (2020) - 5 pages - No description available. - "
        "foo.bar"
    )
    assert event.mock_calls == []


def test_books_no_pagecount(mock_bot, mock_requests, patch_try_shorten, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [
                {
                    "volumeInfo": {
                        "title": "foo",
                        "infoLink": "foo.bar",
                        "authors": ["foo", "bar"],
                        "publisher": "test publisher",
                        "description": "foobar",
                        "publishedDate": "2020-07-05",
                    }
                }
            ],
        },
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == ("\x02foo\x02 by \x02foo\x02 (2020) - foobar - foo.bar")
    assert event.mock_calls == []


def test_books_no_author_or_publisher(
    mock_bot, mock_requests, patch_try_shorten, db
):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [
                {
                    "volumeInfo": {
                        "title": "foo",
                        "infoLink": "foo.bar",
                        "description": "foobar",
                        "publishedDate": "2020-07-05",
                        "pageCount": 5,
                    }
                }
            ],
        },
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == (
        "\x02foo\x02 by \x02Unknown Author\x02 (2020) - 5 pages - foobar - foo.bar"
    )
    assert event.mock_calls == []


def test_books_no_date(mock_bot, mock_requests, patch_try_shorten, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [
                {
                    "volumeInfo": {
                        "title": "foo",
                        "infoLink": "foo.bar",
                        "authors": ["foo", "bar"],
                        "publisher": "test publisher",
                        "description": "foobar",
                        "pageCount": 5,
                    }
                }
            ],
        },
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert res == (
        "\x02foo\x02 by \x02foo\x02 (No Year) - 5 pages - foobar - foo.bar"
    )
    assert event.mock_calls == []


# ---------- rate-limit wiring ----------


def test_books_success_records_event(
    mock_bot, mock_requests, patch_try_shorten, db
):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [{"volumeInfo": {"title": "t", "infoLink": "u"}}],
        },
    )
    assert _bucket_count(db) == 0
    books.books("foo", event.reply, mock_bot, db)
    assert _bucket_count(db) == 1


def test_books_no_results_does_not_record(mock_bot, mock_requests, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={"totalItems": 0},
    )
    books.books("foo", event.reply, mock_bot, db)
    assert _bucket_count(db) == 0


def test_books_http_error_does_not_record(mock_bot, mock_requests, db):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        status=500,
    )
    with pytest.raises(requests.HTTPError):
        books.books("foo", event.reply, mock_bot, db)
    assert _bucket_count(db) == 0


def test_books_daily_cap_blocks(mock_bot, db):
    """When cap is already saturated, a new call returns the cap message and
    does NOT touch the HTTP layer (so no mock_requests responses needed)."""
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    now = datetime.datetime.utcnow()
    for _ in range(books.BOOKS_MAX_RPD):
        db.execute(
            ratelimit.ratelimit_table.insert().values(
                bucket=books.BOOKS_BUCKET, ts=now, weight=1
            )
        )
    db.commit()
    res = books.books("foo", event.reply, mock_bot, db)
    assert "Daily Books cap reached" in res
    assert _bucket_count(db) == books.BOOKS_MAX_RPD


def test_books_daily_cap_resets_after_24h(
    mock_bot, mock_requests, patch_try_shorten, db, freeze_time
):
    mock_bot.config["api_keys"] = {"google": "foo"}
    event = MagicMock()
    now = datetime.datetime.utcnow()
    for _ in range(books.BOOKS_MAX_RPD):
        db.execute(
            ratelimit.ratelimit_table.insert().values(
                bucket=books.BOOKS_BUCKET, ts=now, weight=1
            )
        )
    db.commit()
    assert "Daily" in books.books("foo", event.reply, mock_bot, db)

    freeze_time.tick(delta=timedelta(hours=24, seconds=1))
    mock_requests.add(
        "GET",
        "https://www.googleapis.com/books/v1/volumes?q=foo&key=foo&country=US",
        json={
            "totalItems": 1,
            "items": [{"volumeInfo": {"title": "fresh", "infoLink": "u"}}],
        },
    )
    res = books.books("foo", event.reply, mock_bot, db)
    assert "fresh" in res
