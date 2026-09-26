import time
from unittest.mock import MagicMock, patch

import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

from cloudbot.event import EventType
from plugins import history
from tests.util.mock_conn import MockConn


@pytest.fixture(autouse=True)
def _clean_history_state():
    history.url_results_queue.clear()
    yield
    history.url_results_queue.clear()


@pytest.fixture()
def seen_db(mock_db):
    history.seen_table.create(mock_db.engine)
    return mock_db.session()


@pytest.fixture()
def urls_db(mock_db):
    history.user_urls_table.create(mock_db.engine)
    return mock_db.session()


def make_event(**kwargs):
    event = MagicMock()
    for key, value in kwargs.items():
        setattr(event, key, value)
    return event


# --- is_valid_url ---


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://example.com", True),
        ("https://example.com/path?q=1", True),
        ("example.com", False),
        ("http://", False),
        ("http://" + "a" * 254 + ".com", False),
        ("http://" + "a" * 64 + ".com", False),
        ("http://a..com", False),
        ("http://[::1:8080", False),
    ],
)
def test_is_valid_url(url, expected):
    assert history.is_valid_url(url) is expected


# --- get_url_title ---


def test_get_url_title_invalid_url_returns_none():
    assert history.get_url_title("not a url") is None


def test_get_url_title_returns_stripped_title(mock_requests):
    mock_requests.add(
        responses.GET,
        "http://example.com/",
        body="<html><head><title>  Hello World  </title></head></html>",
        status=200,
        content_type="text/html; charset=utf-8",
    )
    assert history.get_url_title("http://example.com/") == "Hello World"


def test_get_url_title_truncates_long_title(mock_requests):
    long_title = "x" * 150
    mock_requests.add(
        responses.GET,
        "http://example.com/",
        body=f"<html><head><title>{long_title}</title></head></html>",
        status=200,
        content_type="text/html; charset=utf-8",
    )
    result = history.get_url_title("http://example.com/")
    assert result.endswith("... [trunc]")
    assert len(result) == 100 + len(" ... [trunc]")


def test_get_url_title_no_title_tag(mock_requests):
    mock_requests.add(
        responses.GET,
        "http://example.com/",
        body="<html><body>no title here</body></html>",
        status=200,
        content_type="text/html; charset=utf-8",
    )
    assert history.get_url_title("http://example.com/") is None


def test_get_url_title_non_ok_status(mock_requests):
    mock_requests.add(
        responses.GET,
        "http://example.com/",
        body="<html><head><title>Hi</title></head></html>",
        status=404,
    )
    assert history.get_url_title("http://example.com/") is None


def test_get_url_title_request_exception(mock_requests):
    mock_requests.add(
        responses.GET,
        "http://example.com/",
        body=RequestsConnectionError("boom"),
    )
    assert history.get_url_title("http://example.com/") is None


# --- track_user_urls / cleanup_old_urls ---


def test_track_user_urls_ignores_private_message(urls_db):
    event = make_event(
        chan="somenick", content="check http://example.com", conn=MockConn()
    )
    with patch("plugins.history.get_url_title", return_value="Example"):
        history.track_user_urls(event, urls_db)
    assert urls_db.execute(history.user_urls_table.select()).fetchall() == []


def test_track_user_urls_no_urls_in_message(urls_db):
    event = make_event(chan="#chan", content="just chatting", conn=MockConn())
    with patch("plugins.history.get_url_title", return_value="Example"):
        history.track_user_urls(event, urls_db)
    assert urls_db.execute(history.user_urls_table.select()).fetchall() == []


def test_track_user_urls_inserts_row_and_adds_protocol(urls_db):
    conn = MockConn(name="testconn")
    event = make_event(
        chan="#Chan",
        content="check out example.com please",
        nick="Alice",
        conn=conn,
    )
    with patch("plugins.history.get_url_title", return_value="Example Title"):
        history.track_user_urls(event, urls_db)
    rows = urls_db.execute(history.user_urls_table.select()).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row.network == "testconn"
    assert row.chan == "#chan"
    assert row.nick == "alice"
    assert row.url == "http://example.com"
    assert row.title == "Example Title"


def test_track_user_urls_no_title_available(urls_db):
    conn = MockConn()
    event = make_event(
        chan="#chan", content="http://example.com", nick="bob", conn=conn
    )
    with patch("plugins.history.get_url_title", return_value=None):
        history.track_user_urls(event, urls_db)
    row = urls_db.execute(history.user_urls_table.select()).fetchone()
    assert row.title == "No title available"


def test_cleanup_old_urls_removes_oldest(urls_db):
    for i in range(5):
        urls_db.execute(
            history.user_urls_table.insert().values(
                network="net",
                chan="#chan",
                nick="alice",
                url=f"http://example.com/{i}",
                title=f"Title {i}",
                timestamp=float(i),
            )
        )
    urls_db.commit()

    with patch("plugins.history.MAX_URLS_PER_USER", 3):
        history.cleanup_old_urls(urls_db, "net", "#chan", "alice")

    remaining = urls_db.execute(
        history.user_urls_table.select().order_by(
            history.user_urls_table.c.timestamp
        )
    ).fetchall()
    assert [r.timestamp for r in remaining] == [2.0, 3.0, 4.0]


# --- track_seen ---


def test_track_seen_private_message_skipped(seen_db):
    event = make_event(
        chan="alice", content="hello", nick="bob", mask="bob!x@y"
    )
    history.track_seen(event, seen_db)
    assert seen_db.execute(history.seen_table.select()).fetchall() == []


def test_track_seen_sed_message_skipped(seen_db):
    event = make_event(
        chan="#chan", content="s/foo/bar/", nick="bob", mask="bob!x@y"
    )
    history.track_seen(event, seen_db)
    assert seen_db.execute(history.seen_table.select()).fetchall() == []


def test_track_seen_inserts_new_row(seen_db, freeze_time):
    event = make_event(
        chan="#chan", content="hi there", nick="Bob", mask="bob!x@y"
    )
    history.track_seen(event, seen_db)
    row = seen_db.execute(history.seen_table.select()).fetchone()
    assert row.name == "bob"
    assert row.chan == "#chan"
    assert row.quote == "hi there"
    assert row.host == "bob!x@y"


def test_track_seen_updates_existing_row(seen_db, freeze_time):
    event = make_event(
        chan="#chan", content="first", nick="bob", mask="bob!x@y"
    )
    history.track_seen(event, seen_db)
    freeze_time.tick()
    event2 = make_event(
        chan="#chan", content="second", nick="bob", mask="bob!x@y"
    )
    history.track_seen(event2, seen_db)
    rows = seen_db.execute(history.seen_table.select()).fetchall()
    assert len(rows) == 1
    assert rows[0].quote == "second"


# --- chat_tracker ---


def test_chat_tracker_message_event(mock_db, freeze_time):
    history.seen_table.create(mock_db.engine)
    history.user_urls_table.create(mock_db.engine)
    db = mock_db.session()
    conn = MockConn(name="testconn")
    event = make_event(
        type=EventType.message,
        chan="#chan",
        content="hello there",
        nick="bob",
        mask="bob!x@y",
        conn=conn,
    )
    history.chat_tracker(event, db, conn)
    row = db.execute(history.seen_table.select()).fetchone()
    assert row.quote == "hello there"


def test_chat_tracker_action_event_wraps_content(mock_db, freeze_time):
    history.seen_table.create(mock_db.engine)
    history.user_urls_table.create(mock_db.engine)
    db = mock_db.session()
    conn = MockConn(name="testconn")
    event = make_event(
        type=EventType.action,
        chan="#chan",
        content="waves",
        nick="bob",
        mask="bob!x@y",
        conn=conn,
    )
    history.chat_tracker(event, db, conn)
    row = db.execute(history.seen_table.select()).fetchone()
    assert row.quote == "\x01ACTION waves\x01"


# --- seen command ---


def test_seen_asking_about_bot(seen_db):
    conn = MockConn(nick="MyBot")
    event = make_event(conn=conn, is_nick_valid=lambda n: True)
    result = history.seen(
        "MyBot", "alice", "#chan", seen_db, event, lambda n: True
    )
    assert result == "You need to get your eyes checked."


def test_seen_asking_about_self(seen_db):
    result = history.seen(
        "alice",
        "alice",
        "#chan",
        seen_db,
        make_event(conn=MockConn()),
        lambda n: True,
    )
    assert result == "Have you looked in a mirror lately?"


def test_seen_invalid_nick(seen_db):
    result = history.seen(
        "b@d",
        "alice",
        "#chan",
        seen_db,
        make_event(conn=MockConn()),
        lambda n: False,
    )
    assert result == "I can't look up that name, its impossible to use!"


def test_seen_never_seen(seen_db):
    result = history.seen(
        "bob",
        "alice",
        "#chan",
        seen_db,
        make_event(conn=MockConn()),
        lambda n: True,
    )
    assert result == "I've never seen bob talking in this channel."


def test_seen_regular_message(seen_db, freeze_time):
    base = time.time()
    seen_db.execute(
        history.seen_table.insert().values(
            name="bob", time=base, quote="hello world", chan="#chan", host="x"
        )
    )
    seen_db.commit()
    freeze_time.tick(60)
    result = history.seen(
        "bob",
        "alice",
        "#chan",
        seen_db,
        make_event(conn=MockConn()),
        lambda n: True,
    )
    assert result == "bob was last seen 60 seconds ago saying: hello world"


def test_seen_action_message(seen_db, freeze_time):
    base = time.time()
    seen_db.execute(
        history.seen_table.insert().values(
            name="bob",
            time=base,
            quote="\x01ACTION waves\x01",
            chan="#chan",
            host="x",
        )
    )
    seen_db.commit()
    result = history.seen(
        "bob",
        "alice",
        "#chan",
        seen_db,
        make_event(conn=MockConn()),
        lambda n: True,
    )
    assert result == "bob was last seen 0 minutes ago: * bob waves"


# --- lastlink ---


def test_lastlink_no_links(urls_db):
    conn = MockConn(name="testconn")
    assert history.lastlink("", "#chan", conn, urls_db) == "No links found"


def test_lastlink_no_links_for_nick(urls_db):
    conn = MockConn(name="testconn")
    assert (
        history.lastlink("bob", "#chan", conn, urls_db)
        == "No links found for nick: bob"
    )


def test_lastlink_general(urls_db):
    conn = MockConn(name="testconn")
    urls_db.execute(
        history.user_urls_table.insert().values(
            network="testconn",
            chan="#chan",
            nick="bob",
            url="http://example.com",
            title="Example",
            timestamp=1000.0,
        )
    )
    urls_db.commit()
    result = history.lastlink("", "#chan", conn, urls_db)
    assert "bob: Example - http://example.com" in result


def test_lastlink_for_specific_nick(urls_db):
    conn = MockConn(name="testconn")
    urls_db.execute(
        history.user_urls_table.insert().values(
            network="testconn",
            chan="#chan",
            nick="bob",
            url="http://example.com",
            title=None,
            timestamp=1000.0,
        )
    )
    urls_db.commit()
    result = history.lastlink("bob", "#chan", conn, urls_db)
    assert "bob: No title available - http://example.com" in result


# --- userlinks ---


def test_userlinks_no_links(urls_db):
    conn = MockConn(name="testconn")
    result = history.userlinks("", "#chan", conn, urls_db, "alice")
    assert result == "No links found"


def test_userlinks_no_links_for_nick(urls_db):
    conn = MockConn(name="testconn")
    result = history.userlinks("bob", "#chan", conn, urls_db, "alice")
    assert result == "No links found for nick: bob"


def _seed_urls(db, conn_name, chan, nick, count):
    for i in range(count):
        db.execute(
            history.user_urls_table.insert().values(
                network=conn_name,
                chan=chan,
                nick=nick,
                url=f"http://example.com/{i}",
                title=f"Title {i}",
                timestamp=float(i),
            )
        )
    db.commit()


def test_userlinks_default_nick_shows_recent_and_queues(urls_db):
    conn = MockConn(name="testconn")
    _seed_urls(urls_db, "testconn", "#chan", "alice", 5)
    result = history.userlinks("", "#chan", conn, urls_db, "alice")
    assert result.startswith("Recent links in channel: (showing 3 of 5")
    assert "1. Title 4 - http://example.com/4" in result
    assert len(history.url_results_queue["#chan"]["alice"]) == 5


def test_userlinks_for_nick_no_pagination_hint(urls_db):
    conn = MockConn(name="testconn")
    _seed_urls(urls_db, "testconn", "#chan", "bob", 2)
    result = history.userlinks("bob", "#chan", conn, urls_db, "alice")
    assert result.startswith("Recent links for bob: ")
    assert "(showing" not in result


def test_userlinks_truncates_long_titles(urls_db):
    conn = MockConn(name="testconn")
    urls_db.execute(
        history.user_urls_table.insert().values(
            network="testconn",
            chan="#chan",
            nick="alice",
            url="http://example.com",
            title="x" * 60,
            timestamp=1.0,
        )
    )
    urls_db.commit()
    result = history.userlinks("", "#chan", conn, urls_db, "alice")
    assert "..." in result


# --- urls_next ---


def test_urls_next_no_queue():
    # url_results_queue auto-vivifies missing keys to an empty list (see
    # cloudbot.util.queue.Queue), so this never hits the KeyError branch and
    # falls through to the same message as an emptied queue.
    result = history.urls_next("", "#chan", MockConn(), "alice")
    assert result == "No [more] results found."


def test_urls_next_empty_queue():
    history.url_results_queue["#chan"]["alice"] = []
    result = history.urls_next("", "#chan", MockConn(), "alice")
    assert result == "No [more] results found."


def test_urls_next_exhausts_queue():
    history.url_results_queue["#chan"]["alice"] = [
        ("http://a", "A", 1.0),
        ("http://b", "B", 2.0),
    ]
    result = history.urls_next("", "#chan", MockConn(), "alice")
    assert result == "No more results found."
    assert history.url_results_queue["#chan"]["alice"] == []


def test_urls_next_returns_next_page(urls_db):
    conn = MockConn(name="testconn")
    _seed_urls(urls_db, "testconn", "#chan", "alice", 5)
    history.userlinks("", "#chan", conn, urls_db, "alice")
    result = history.urls_next("", "#chan", conn, "alice")
    assert result.startswith("More links in channel: ")
    assert "4. Title 1 - http://example.com/1" in result
    assert "5. Title 0 - http://example.com/0" in result


# --- searchword (said) ---


def test_searchword_no_history():
    conn = MockConn()
    result = history.searchword("bob hello", "#chan", conn)
    assert result == "There is no history for this channel."


def test_searchword_missing_args():
    conn = MockConn()
    conn.history["#chan"] = [("bob", 1.0, "hi")]
    result = history.searchword("bob", "#chan", conn)
    assert result == "Please provide a nick and a search string."


def test_searchword_finds_match():
    conn = MockConn()
    conn.history["#chan"] = [
        ("bob", 900.0, "hello world"),
        ("bob", 1000.0, "the current invocation"),
    ]
    result = history.searchword("bob world", "#chan", conn)
    assert "bob: hello \x02world\x02" in result


def test_searchword_wildcard_nick():
    conn = MockConn()
    conn.history["#chan"] = [
        ("bob", 900.0, "special text here"),
        ("alice", 1000.0, "the current invocation"),
    ]
    result = history.searchword("* special", "#chan", conn)
    assert "bob: \x02special\x02 text here" in result


def test_searchword_action_message_transform():
    conn = MockConn()
    conn.history["#chan"] = [
        ("bob", 900.0, "\x01ACTION waves hello\x01"),
        ("bob", 1000.0, "invocation"),
    ]
    result = history.searchword("bob hello", "#chan", conn)
    assert "* waves \x02hello\x02" in result


def test_searchword_not_found():
    conn = MockConn()
    conn.history["#chan"] = [("bob", 1000.0, "invocation")]
    result = history.searchword("bob missing", "#chan", conn)
    assert (
        result
        == "Seems like bob hasn't said anything containing 'missing' recently"
    )


# --- now / utc ---


def test_now_returns_formatted_local_time(freeze_time):
    assert history.now("", "#chan", MockConn()) == "2019-08-22 13:14:36"


def test_utc_returns_formatted_utc_time(freeze_time):
    assert history.utc("", "#chan", MockConn()) == "2019-08-22 18:14:36"
