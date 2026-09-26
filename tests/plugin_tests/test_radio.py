from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from responses import matchers

from plugins import radio
from tests.util.mock_conn import MockConn, account_conn


@pytest.fixture(autouse=True)
def _reset_radio_state():
    radio.last_sent_messages.clear()
    radio.stream_token_cache.clear()
    radio.queue_additions_tracker.clear()
    yield
    radio.last_sent_messages.clear()
    radio.stream_token_cache.clear()
    radio.queue_additions_tracker.clear()


def make_bot(radio_config=None, connections=None, webhooks=None):
    config = {"plugins": {"radio": radio_config or {}}}
    if webhooks is not None:
        config["webhooks"] = webhooks
    return SimpleNamespace(config=config, connections=connections or {})


RADIO_CFG = {"api_url": "http://radio.example.com:8000", "api_token": "tok123"}


# --- check_user_authenticated ---


def test_check_user_authenticated_no_nick():
    assert "verify" in radio.check_user_authenticated(MockConn(), "")


def test_check_user_authenticated_no_conn():
    assert "verify" in radio.check_user_authenticated(None, "bob")


def test_check_user_authenticated_ok():
    conn = account_conn("bob", "bob_account")
    assert radio.check_user_authenticated(conn, "bob") is None


def test_check_user_authenticated_not_identified():
    conn = MockConn()
    result = radio.check_user_authenticated(conn, "bob")
    assert result is not None
    assert "authenticated with services" in result


def test_check_user_authenticated_exception():
    result = radio.check_user_authenticated(object(), "bob")
    assert "Unable to verify" in result


# --- check_queue_rate_limit / record_queue_addition ---


def test_check_queue_rate_limit_fresh_unauthenticated():
    conn = MockConn()
    allowed, err = radio.check_queue_rate_limit(conn, "bob", "#chan")
    assert allowed is True
    assert err is None
    assert radio.queue_additions_tracker[("#chan", "bob")] == []


def test_record_queue_addition_lowercases_nick():
    radio.record_queue_addition("Bob", "#chan")
    assert len(radio.queue_additions_tracker[("#chan", "bob")]) == 1


def test_check_queue_rate_limit_exceeded_unauthenticated_seconds():
    conn = MockConn()
    now = radio.time.time()
    oldest = now - radio.QUEUE_RATE_LIMIT_WINDOW + 30
    radio.queue_additions_tracker[("#chan", "bob")] = [oldest] + [now] * 9
    allowed, err = radio.check_queue_rate_limit(conn, "bob", "#chan")
    assert allowed is False
    assert "Non-authenticated users can add 10 songs per hour" in err
    assert "Used: 10/10" in err
    time_str = err.split("Try again in ")[1].split(".")[0]
    assert "m" not in time_str
    assert time_str.endswith("s")


def test_check_queue_rate_limit_exceeded_authenticated_minutes():
    conn = account_conn("alice", "alice_account")
    now = radio.time.time()
    oldest = now - radio.QUEUE_RATE_LIMIT_WINDOW + 90
    radio.queue_additions_tracker[("#chan", "alice")] = [oldest] + [now] * 19
    allowed, err = radio.check_queue_rate_limit(conn, "alice", "#chan")
    assert allowed is False
    assert "Authenticated users can add 20 songs per hour" in err
    assert "m" in err.split("Try again in ")[1].split(".")[0]


def test_check_queue_rate_limit_prunes_stale_entries():
    conn = MockConn()
    now = radio.time.time()
    stale = now - radio.QUEUE_RATE_LIMIT_WINDOW - 10
    radio.queue_additions_tracker[("#chan", "bob")] = [stale, now]
    allowed, _ = radio.check_queue_rate_limit(conn, "bob", "#chan")
    assert allowed is True
    assert radio.queue_additions_tracker[("#chan", "bob")] == [now]


# --- get_radio_url ---


def test_get_radio_url_with_port_and_path():
    assert (
        radio.get_radio_url({"api_url": "http://host:8080/some/path"})
        == "http://host:8080"
    )


def test_get_radio_url_missing():
    assert radio.get_radio_url({}) == "://"


# --- convert_suno_url ---


def test_convert_suno_url_valid():
    url = "https://suno.com/song/ea742086-d37a-48c6-8d9d-82aea36c70ed?sh=abc"
    assert (
        radio.convert_suno_url(url)
        == "https://cdn1.suno.ai/ea742086-d37a-48c6-8d9d-82aea36c70ed.mp3"
    )


def test_convert_suno_url_not_suno():
    assert radio.convert_suno_url("https://example.com/song/abc") is None


def test_convert_suno_url_not_song_path():
    assert radio.convert_suno_url("https://suno.com/artist/abc") is None


def test_convert_suno_url_root_path():
    assert radio.convert_suno_url("https://suno.com/") is None


# --- scrape_suno_metadata ---


def test_scrape_suno_metadata_title_and_artist(mock_requests):
    url = "https://suno.com/song/abc"
    mock_requests.add(
        "GET",
        url,
        body="<html><head><title>My Song by The Artist | Suno</title></head></html>",
        content_type="text/html",
    )
    title, artist = radio.scrape_suno_metadata(url)
    assert title == "My Song"
    assert artist == "The Artist"


def test_scrape_suno_metadata_no_artist(mock_requests):
    url = "https://suno.com/song/abc"
    mock_requests.add(
        "GET",
        url,
        body="<html><head><title>Solo Track | Suno</title></head></html>",
        content_type="text/html",
    )
    title, artist = radio.scrape_suno_metadata(url)
    assert title == "Solo Track"
    assert artist == "Unknown Artist"


def test_scrape_suno_metadata_no_suno_suffix(mock_requests):
    url = "https://suno.com/song/abc"
    mock_requests.add(
        "GET",
        url,
        body="<html><head><title>Just A Page Title</title></head></html>",
        content_type="text/html",
    )
    title, artist = radio.scrape_suno_metadata(url)
    assert title == "Just A Page Title"
    assert artist == "Unknown Artist"


def test_scrape_suno_metadata_no_title_tag(mock_requests):
    url = "https://suno.com/song/abc"
    mock_requests.add(
        "GET", url, body="<html><head></head></html>", content_type="text/html"
    )
    title, artist = radio.scrape_suno_metadata(url)
    assert title == "Suno Track"
    assert artist == "Unknown Artist"


def test_scrape_suno_metadata_request_error(mock_requests):
    url = "https://suno.com/song/abc"
    mock_requests.add(
        "GET", url, body=requests.exceptions.ConnectionError("boom")
    )
    title, artist = radio.scrape_suno_metadata(url)
    assert title == "Suno Track"
    assert artist == "Unknown Artist"


# --- process_song_url ---


def test_process_song_url_suno(mock_requests):
    url = "https://suno.com/song/abc-123"
    mock_requests.add(
        "GET",
        url,
        body="<html><head><title>Song by Artist | Suno</title></head></html>",
        content_type="text/html",
    )
    meta = radio.process_song_url(url)
    assert meta.url == "https://cdn1.suno.ai/abc-123.mp3"
    assert meta.reference_url == url
    assert meta.title == "Song"
    assert meta.artist == "Artist"
    assert meta.genre == "AI Generated by suno.com"


def test_process_song_url_suno_unparseable_path():
    url = "https://suno.com/"
    meta = radio.process_song_url(url)
    assert meta.url == url
    assert meta.reference_url == url
    assert meta.title is None


def test_process_song_url_other():
    url = "https://youtube.com/watch?v=abc"
    meta = radio.process_song_url(url)
    assert meta.url == url
    assert meta.reference_url == url
    assert meta.title is None
    assert meta.artist is None


# --- fetch_current_metadata ---


def test_fetch_current_metadata_no_api_url():
    assert radio.fetch_current_metadata({}) is None


def test_fetch_current_metadata_success(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {"title": "T", "artist": "A"}},
        match=[matchers.header_matcher({"Authorization": "Bearer tok123"})],
    )
    data = radio.fetch_current_metadata(RADIO_CFG)
    assert data["source"] == "user"


def test_fetch_current_metadata_error(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        body=requests.exceptions.ConnectionError("boom"),
    )
    assert radio.fetch_current_metadata(RADIO_CFG) is None


# --- format_livestream_message ---


def test_format_livestream_started_full_show():
    msg = radio.format_livestream_message(
        "livestream_started",
        {
            "source": "livestream",
            "metadata": {
                "title": "Some Song",
                "artist": "Bob",
                "show_name": "Show1",
                "show_user": "Alice",
            },
        },
        "http://radio",
    )
    assert msg == (
        "🎬 Alice is now live! - Show1. ♫ Bob - Some Song | Listen: http://radio"
    )


def test_format_livestream_started_unknown_show():
    msg = radio.format_livestream_message(
        "livestream_started",
        {
            "source": "livestream",
            "metadata": {
                "title": "Testing Stream",
                "show_name": "unknown",
                "show_user": "unknown",
            },
        },
        "http://radio",
    )
    assert msg == "🎬 Livestream started | Listen: http://radio"


def test_format_livestream_started_no_track_no_artist():
    msg = radio.format_livestream_message(
        "livestream_started",
        {
            "source": "livestream",
            "metadata": {"title": "My Song", "show_user": "Alice"},
        },
        "http://radio",
    )
    assert "♫ My Song" in msg
    assert "Alice is now live!" in msg


def test_format_livestream_started_livestream_no_show_info():
    msg = radio.format_livestream_message(
        "livestream_started",
        {"source": "livestream", "metadata": {}},
        "http://radio",
    )
    assert msg == "📺 Livestream started | Listen: http://radio"


def test_format_livestream_started_user_source():
    msg = radio.format_livestream_message(
        "livestream_started",
        {"source": "user", "metadata": {"title": "T", "artist": "A"}},
        "http://radio",
    )
    assert msg == "🎬 Livestream started: ♫ A - T | Listen: http://radio"


def test_format_livestream_started_other_source():
    msg = radio.format_livestream_message(
        "livestream_started",
        {"source": "fallback", "metadata": {}},
        "http://radio",
    )
    assert msg == "📺 Livestream started | Listen: http://radio"


def test_format_livestream_ended_fallback():
    msg = radio.format_livestream_message(
        "livestream_ended",
        {"source": "fallback", "metadata": {"title": "T", "artist": "A"}},
        "http://radio",
    )
    assert msg == "📻 Livestream ended, back to fallback: ♫ A - T"


def test_format_livestream_ended_user():
    msg = radio.format_livestream_message(
        "livestream_ended",
        {"source": "user", "metadata": {"title": "T", "artist": "A"}},
        "http://radio",
    )
    assert msg == "🎵 Livestream ended, now playing: ♫ A - T"


def test_format_livestream_ended_other():
    msg = radio.format_livestream_message(
        "livestream_ended", {"source": "other", "metadata": {}}, "http://radio"
    )
    assert msg == "📺 Livestream ended"


def test_format_livestream_message_unknown_event():
    assert (
        radio.format_livestream_message("song_changed", {}, "http://radio")
        is None
    )


# --- send_debounced_message ---


def test_send_debounced_message_no_metadata(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        body=requests.exceptions.ConnectionError("boom"),
    )
    bot_instance = make_bot(RADIO_CFG)
    radio.send_debounced_message(bot_instance, "#chan", "livestream_started")
    assert radio.last_sent_messages == {}


def test_send_debounced_message_no_message(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {}},
    )
    bot_instance = make_bot(RADIO_CFG)
    radio.send_debounced_message(bot_instance, "#chan", "song_changed")
    assert radio.last_sent_messages == {}


def test_send_debounced_message_dedup(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {"title": "T", "artist": "A"}},
    )
    radio.last_sent_messages["#chan"] = {
        "title": "T",
        "artist": "A",
        "source": "user",
        "event": "livestream_started",
    }
    conn = MagicMock(connected=True)
    bot_instance = make_bot(RADIO_CFG, connections={"gobot": conn})
    radio.send_debounced_message(bot_instance, "#chan", "livestream_started")
    conn.message.assert_not_called()


def test_send_debounced_message_no_connection(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {"title": "T", "artist": "A"}},
    )
    bot_instance = make_bot(RADIO_CFG, connections={})
    radio.send_debounced_message(bot_instance, "#chan", "livestream_started")
    assert radio.last_sent_messages == {}


def test_send_debounced_message_not_connected(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {"title": "T", "artist": "A"}},
    )
    conn = MagicMock(connected=False)
    bot_instance = make_bot(RADIO_CFG, connections={"gobot": conn})
    radio.send_debounced_message(bot_instance, "#chan", "livestream_started")
    conn.message.assert_not_called()


def test_send_debounced_message_success(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {"title": "T", "artist": "A"}},
    )
    conn = MagicMock(connected=True)
    bot_instance = make_bot(RADIO_CFG, connections={"gobot": conn})
    radio.send_debounced_message(bot_instance, "#chan", "livestream_started")
    conn.message.assert_called_once_with(
        "#chan",
        "🎬 Livestream started: ♫ A - T | Listen: http://radio.example.com:8000",
    )
    assert radio.last_sent_messages["#chan"]["title"] == "T"


def test_send_debounced_message_custom_connection_name(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {"title": "T", "artist": "A"}},
    )
    conn = MagicMock(connected=True)
    cfg = dict(RADIO_CFG, connection="mynet")
    bot_instance = make_bot(cfg, connections={"mynet": conn})
    radio.send_debounced_message(bot_instance, "#chan", "livestream_started")
    conn.message.assert_called_once()


# --- handle_radio_webhook ---


def test_handle_radio_webhook_livestream_schedules_timer():
    bot_instance = make_bot(
        {
            "channels": {
                "#a": {"events": ["livestream_started"]},
                "#b": {"events": ["livestream_ended"]},
            }
        }
    )
    with patch("plugins.radio.threading.Timer") as timer_cls:
        timer_instance = MagicMock()
        timer_cls.return_value = timer_instance
        radio.handle_radio_webhook(
            bot_instance, {"event_type": "livestream_started"}
        )
    timer_cls.assert_called_once_with(
        5.0,
        radio.send_debounced_message,
        args=(bot_instance, "#a", "livestream_started"),
    )
    assert timer_instance.daemon is True
    timer_instance.start.assert_called_once()


def test_handle_radio_webhook_song_changed_no_data():
    bot_instance = make_bot(
        {}, connections={"gobot": MagicMock(connected=True)}
    )
    radio.handle_radio_webhook(
        bot_instance, {"event_type": "song_changed", "data": {}}
    )
    assert radio.last_sent_messages == {}


def test_handle_radio_webhook_no_connection():
    bot_instance = make_bot(
        {"channels": {"#a": {"events": ["song_changed"]}}}, connections={}
    )
    radio.handle_radio_webhook(
        bot_instance,
        {
            "event_type": "song_changed",
            "data": {"title": "T", "artist": "A", "playlist": "user"},
        },
    )
    assert radio.last_sent_messages == {}


def test_handle_radio_webhook_not_connected():
    conn = MagicMock(connected=False)
    bot_instance = make_bot(
        {"channels": {"#a": {"events": ["song_changed"]}}},
        connections={"gobot": conn},
    )
    radio.handle_radio_webhook(
        bot_instance,
        {
            "event_type": "song_changed",
            "data": {"title": "T", "artist": "A", "playlist": "user"},
        },
    )
    conn.message.assert_not_called()


def test_handle_radio_webhook_sends_to_allowed_channels_only():
    conn = MagicMock(connected=True)
    bot_instance = make_bot(
        {
            "channels": {
                "#a": {"events": ["song_changed"]},
                "#b": {"events": ["queue_switched"]},
            }
        },
        connections={"gobot": conn},
    )
    radio.handle_radio_webhook(
        bot_instance,
        {
            "event_type": "song_changed",
            "data": {"title": "T", "artist": "A", "playlist": "user"},
        },
    )
    conn.message.assert_called_once_with("#a", "🎵 Now playing: ♫ A - T")
    assert radio.last_sent_messages["#a"]["title"] == "T"
    assert "#b" not in radio.last_sent_messages


def test_handle_radio_webhook_dedup_skips_repeat():
    conn = MagicMock(connected=True)
    bot_instance = make_bot(
        {"channels": {"#a": {"events": ["song_changed"]}}},
        connections={"gobot": conn},
    )
    payload = {
        "event_type": "song_changed",
        "data": {"title": "T", "artist": "A", "playlist": "user"},
    }
    radio.handle_radio_webhook(bot_instance, payload)
    radio.handle_radio_webhook(bot_instance, payload)
    conn.message.assert_called_once()


# --- format_radio_message ---


def test_format_radio_message_song_changed_user():
    msg = radio.format_radio_message(
        "song_changed",
        {"data": {"title": "T", "artist": "A", "playlist": "user"}},
        {},
    )
    assert msg == "🎵 Now playing: ♫ A - T"


def test_format_radio_message_song_changed_fallback():
    msg = radio.format_radio_message(
        "song_changed",
        {"data": {"title": "T", "artist": "A", "playlist": "fallback"}},
        {},
    )
    assert msg == "📻 Fallback playing: ♫ A - T"


def test_format_radio_message_song_changed_unknown_playlist():
    msg = radio.format_radio_message(
        "song_changed",
        {"data": {"title": "T", "artist": "A", "playlist": "livestream"}},
        {},
    )
    assert msg is None


def test_format_radio_message_song_changed_missing_both():
    msg = radio.format_radio_message(
        "song_changed", {"data": {"playlist": "user"}}, {}
    )
    assert msg is None


def test_format_radio_message_song_changed_missing_title_uses_fallback():
    msg = radio.format_radio_message(
        "song_changed",
        {"data": {"artist": "A", "playlist": "user"}},
        {},
    )
    assert msg == "🎵 Now playing: ♫ A - Unknown Track"


def test_format_radio_message_queue_switched_to_fallback():
    msg = radio.format_radio_message(
        "queue_switched",
        {"data": {"from_source": "user", "to_source": "fallback"}},
        {},
    )
    assert msg == "🔄 Queue empty, switched to fallback"


def test_format_radio_message_queue_switched_to_user():
    msg = radio.format_radio_message(
        "queue_switched",
        {"data": {"from_source": "fallback", "to_source": "user"}},
        {},
    )
    assert msg == "🔄 Switched from fallback to user queue"


def test_format_radio_message_queue_switched_generic():
    msg = radio.format_radio_message(
        "queue_switched",
        {"data": {"from_source": "livestream", "to_source": "user"}},
        {},
    )
    assert msg == "🔄 Source changed: livestream → user"


def test_format_radio_message_recording_done_with_url():
    msg = radio.format_radio_message(
        "livestream_recording_done",
        {
            "data": {
                "title": "T",
                "artist": "A",
                "duration_seconds": 125,
                "recording_url": "/rec/1.mp3",
            }
        },
        {"api_url": "http://radio"},
    )
    assert msg == "💾 Recording saved: ♫ A - T (2m 5s) | http://radio/rec/1.mp3"


def test_format_radio_message_recording_done_short_duration():
    msg = radio.format_radio_message(
        "livestream_recording_done",
        {
            "data": {
                "duration_seconds": 5,
                "recording_url": "",
            }
        },
        {"api_url": ""},
    )
    assert msg == "💾 Recording saved: ♫ Unknown - Untitled (5s) | "


def test_format_radio_message_unknown_event():
    assert radio.format_radio_message("mystery", {}, {}) is None


# --- rsource ---


def test_radio_source_not_configured():
    bot_instance = make_bot({})
    assert radio.radio_source(bot_instance) == "Radio not configured."


def test_radio_source_no_reference(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"metadata": {"title": "T", "artist": "A"}},
    )
    bot_instance = make_bot(RADIO_CFG)
    assert (
        radio.radio_source(bot_instance)
        == "🔗 No source URL available for the current track."
    )


def test_radio_source_success(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={
            "metadata": {
                "title": "T",
                "artist": "A",
                "reference_url": "http://src",
            }
        },
    )
    bot_instance = make_bot(RADIO_CFG)
    assert radio.radio_source(bot_instance) == "🔗 Source: A - T | http://src"


# --- radio command ---


def test_radio_command_not_configured():
    bot_instance = make_bot({})
    assert "not configured" in radio.radio(bot_instance)


def test_radio_command_fallback(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={
            "source": "fallback",
            "metadata": {"title": "T", "artist": "A", "genre": "Rock"},
        },
    )
    bot_instance = make_bot(RADIO_CFG)
    result = radio.radio(bot_instance)
    assert result.startswith("📻 Now Playing [Fallback]:")
    assert "🎸 Genre: Rock" in result
    assert "♫ A - T" in result


def test_radio_command_user_queue(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "user", "metadata": {"title": "T", "artist": "A"}},
    )
    bot_instance = make_bot(RADIO_CFG)
    result = radio.radio(bot_instance)
    assert result.startswith("🎵 Now Playing [Queue]:")


def test_radio_command_livestream_with_show(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={
            "source": "livestream",
            "metadata": {
                "title": "T",
                "artist": "A",
                "show_user": "Alice",
                "show_name": "Show1",
                "description": "desc here",
            },
        },
    )
    bot_instance = make_bot(RADIO_CFG)
    result = radio.radio(bot_instance)
    assert "👤 Alice" in result
    assert "📺 Show1" in result
    assert "ℹ️ desc here" in result


def test_radio_command_unknown_source(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/metadata/now",
        json={"source": "weird", "metadata": {}},
    )
    bot_instance = make_bot(RADIO_CFG)
    result = radio.radio(bot_instance)
    assert result.startswith("🎶 Now Playing [Weird]:")


# --- upcoming ---


def test_show_upcoming_not_configured():
    bot_instance = make_bot({})
    assert "not configured" in radio.show_upcoming(bot_instance)


def test_show_upcoming_empty(mock_requests):
    mock_requests.add(
        "GET", "http://radio.example.com:8000/queue/list", json=[]
    )
    bot_instance = make_bot(RADIO_CFG)
    assert radio.show_upcoming(bot_instance) == "📭 Queue is empty"


def test_show_upcoming_lists_songs(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/queue/list",
        json=[
            {"title": "T1", "artist": "A1", "playlist": "user"},
            {"title": "T2", "artist": "A2", "playlist": "fallback"},
            {"title": "T3", "playlist": "other"},
        ],
    )
    bot_instance = make_bot(RADIO_CFG)
    result = radio.show_upcoming(bot_instance)
    assert "1. 👤 A1 - T1" in result
    assert "2. 📻 A2 - T2" in result
    assert "3. 🎶 Unknown Artist - T3" in result


def test_show_upcoming_http_error_with_response(mock_requests):
    mock_requests.add(
        "GET", "http://radio.example.com:8000/queue/list", status=500
    )
    bot_instance = make_bot(RADIO_CFG)
    assert "HTTP 500" in radio.show_upcoming(bot_instance)


def test_show_upcoming_http_error_no_response():
    err = requests.HTTPError("boom")
    err.response = None
    with patch("plugins.radio.requests.get", side_effect=err):
        bot_instance = make_bot(RADIO_CFG)
        result = radio.show_upcoming(bot_instance)
    assert "boom" in result


def test_show_upcoming_request_exception(mock_requests):
    mock_requests.add(
        "GET",
        "http://radio.example.com:8000/queue/list",
        body=requests.exceptions.ConnectionError("net down"),
    )
    bot_instance = make_bot(RADIO_CFG)
    result = radio.show_upcoming(bot_instance)
    assert "Failed to fetch queue" in result


# --- stream ---


def test_stream_auth_required():
    conn = MockConn()
    bot_instance = make_bot(RADIO_CFG)
    result = radio.stream(bot_instance, conn, "bob", MagicMock())
    assert "authenticated" in result


def test_stream_cached():
    conn = account_conn("bob", "bob_account")
    radio.stream_token_cache["bob"] = True
    bot_instance = make_bot(RADIO_CFG)
    result = radio.stream(bot_instance, conn, "bob", MagicMock())
    assert "once per hour" in result


def test_stream_not_configured():
    conn = account_conn("bob", "bob_account")
    bot_instance = make_bot({})
    result = radio.stream(bot_instance, conn, "bob", MagicMock())
    assert "not configured" in result


def test_stream_success(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/livestream/token",
        json={"token": "abc123", "expires_at": "later"},
    )
    conn = account_conn("bob", "bob_account")
    bot_instance = make_bot(RADIO_CFG)
    message = MagicMock()
    result = radio.stream(bot_instance, conn, "bob", message)
    assert result == "✅ Livestream credentials sent via private message!"
    assert message.call_count == 4
    assert "abc123" in message.call_args_list[1].args[0]
    assert radio.stream_token_cache["bob"] is True


def test_stream_http_error_with_detail(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/livestream/token",
        json={"detail": "quota exceeded"},
        status=400,
    )
    conn = account_conn("bob", "bob_account")
    bot_instance = make_bot(RADIO_CFG)
    result = radio.stream(bot_instance, conn, "bob", MagicMock())
    assert "quota exceeded" in result


def test_stream_http_error_non_json(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/livestream/token",
        body="not json",
        status=503,
        content_type="text/plain",
    )
    conn = account_conn("bob", "bob_account")
    bot_instance = make_bot(RADIO_CFG)
    result = radio.stream(bot_instance, conn, "bob", MagicMock())
    assert "HTTP 503" in result


def test_stream_http_error_no_response():
    err = requests.HTTPError("boom")
    err.response = None
    conn = account_conn("bob", "bob_account")
    bot_instance = make_bot(RADIO_CFG)
    with patch("plugins.radio.requests.post", side_effect=err):
        result = radio.stream(bot_instance, conn, "bob", MagicMock())
    assert "boom" in result


def test_stream_request_exception(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/livestream/token",
        body=requests.exceptions.ConnectionError("down"),
    )
    conn = account_conn("bob", "bob_account")
    bot_instance = make_bot(RADIO_CFG)
    result = radio.stream(bot_instance, conn, "bob", MagicMock())
    assert "Failed to create stream token" in result


# --- add_url_to_queue / queue commands ---


def test_add_url_to_queue_rate_limited():
    conn = MockConn()
    now = radio.time.time()
    radio.queue_additions_tracker[("#chan", "bob")] = [now] * 10
    result = radio.add_url_to_queue(
        "http://x", "user", RADIO_CFG, MagicMock(), conn, "bob", "#chan"
    )
    assert "Rate limit exceeded" in result


def test_add_url_to_queue_not_configured():
    conn = MockConn()
    result = radio.add_url_to_queue(
        "http://x", "user", {}, MagicMock(), conn, "bob", "#chan"
    )
    assert "not configured" in result


def test_add_url_to_queue_success_with_metadata(mock_requests):
    mock_requests.add(
        "GET",
        "https://suno.com/song/abc",
        body="<html><head><title>Song by Artist | Suno</title></head></html>",
        content_type="text/html",
    )
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/queue/add",
        json={"song_id": "42"},
    )
    conn = MockConn()
    event = MagicMock()
    result = radio.add_url_to_queue(
        "https://suno.com/song/abc",
        "user",
        RADIO_CFG,
        event,
        conn,
        "bob",
        "#chan",
    )
    assert result == "✅ Added to user queue: Artist - Song"
    event.reply.assert_called_once_with("⏳ Adding to user queue...")
    assert len(radio.queue_additions_tracker[("#chan", "bob")]) == 1
    sent_body = mock_requests.calls[-1].request.body
    assert b"Song" in sent_body
    assert b"Artist" in sent_body


def test_add_url_to_queue_success_fallback_no_metadata(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/queue/add",
        json={"song_id": "song-99"},
    )
    conn = MockConn()
    result = radio.add_url_to_queue(
        "https://youtube.com/watch?v=1",
        "fallback",
        RADIO_CFG,
        MagicMock(),
        conn,
        "bob",
        "#chan",
    )
    assert result == "✅ Added to fallback playlist: song-99"


def test_add_url_to_queue_http_error_with_detail(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/queue/add",
        json={"detail": "bad url"},
        status=400,
    )
    conn = MockConn()
    result = radio.add_url_to_queue(
        "http://x", "user", RADIO_CFG, MagicMock(), conn, "bob", "#chan"
    )
    assert "bad url" in result


def test_add_url_to_queue_http_error_non_json(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/queue/add",
        body="oops",
        status=500,
        content_type="text/plain",
    )
    conn = MockConn()
    result = radio.add_url_to_queue(
        "http://x", "user", RADIO_CFG, MagicMock(), conn, "bob", "#chan"
    )
    assert "HTTP 500" in result


def test_add_url_to_queue_http_error_no_response():
    err = requests.HTTPError("boom")
    err.response = None
    conn = MockConn()
    with patch("plugins.radio.requests.post", side_effect=err):
        result = radio.add_url_to_queue(
            "http://x", "user", RADIO_CFG, MagicMock(), conn, "bob", "#chan"
        )
    assert "boom" in result


def test_add_url_to_queue_request_exception(mock_requests):
    mock_requests.add(
        "POST",
        "http://radio.example.com:8000/admin/queue/add",
        body=requests.exceptions.ConnectionError("down"),
    )
    conn = MockConn()
    result = radio.add_url_to_queue(
        "http://x", "user", RADIO_CFG, MagicMock(), conn, "bob", "#chan"
    )
    assert "Failed to add to user queue" in result


def test_queue_add_empty_text():
    result = radio.queue_add(
        "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert "Usage" in result


def test_queue_add_delegates():
    with patch("plugins.radio.add_url_to_queue", return_value="ok") as add_mock:
        result = radio.queue_add(
            " http://x ",
            MagicMock(),
            make_bot(RADIO_CFG),
            MockConn(),
            "bob",
            "#chan",
        )
    assert result == "ok"
    assert add_mock.call_args.args[0] == "http://x"
    assert add_mock.call_args.args[1] == "user"


def test_admin_queue_add_empty_text():
    result = radio.admin_queue_add(
        "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert "Usage" in result


def test_admin_queue_add_requires_auth():
    result = radio.admin_queue_add(
        "http://x", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert "authenticated" in result


def test_admin_queue_add_delegates():
    conn = account_conn("bob", "bob_account")
    with patch("plugins.radio.add_url_to_queue", return_value="ok") as add_mock:
        result = radio.admin_queue_add(
            "http://x", MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
        )
    assert result == "ok"
    assert add_mock.call_args.args[1] == "fallback"


def test_queue_add_youtube_no_recent():
    result = radio.queue_add_youtube(
        "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert result == (
        "❌ No recent YouTube search found. Use .yt <query> first to search for a video."
    )


def test_queue_add_youtube_no_recent_other_nick():
    result = radio.queue_add_youtube(
        "alice", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert "found for alice" in result


def test_queue_add_youtube_delegates():
    radio.last_youtube_url[("#chan", "bob")] = "http://yt"
    try:
        with patch(
            "plugins.radio.add_url_to_queue", return_value="ok"
        ) as add_mock:
            result = radio.queue_add_youtube(
                "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
            )
        assert result == "ok"
        assert add_mock.call_args.args[0] == "http://yt"
        assert add_mock.call_args.args[1] == "user"
    finally:
        radio.last_youtube_url.pop(("#chan", "bob"), None)


def test_queue_add_gse_no_recent():
    result = radio.queue_add_gse(
        "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert "No recent Google search" in result


def test_queue_add_gse_delegates():
    radio.last_gse_url[("#chan", "bob")] = "http://gse"
    try:
        with patch(
            "plugins.radio.add_url_to_queue", return_value="ok"
        ) as add_mock:
            result = radio.queue_add_gse(
                "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
            )
        assert result == "ok"
        assert add_mock.call_args.args[0] == "http://gse"
    finally:
        radio.last_gse_url.pop(("#chan", "bob"), None)


def test_admin_queue_add_youtube_requires_auth():
    result = radio.admin_queue_add_youtube(
        "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert "authenticated" in result


def test_admin_queue_add_youtube_no_recent():
    conn = account_conn("bob", "bob_account")
    result = radio.admin_queue_add_youtube(
        "", MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
    )
    assert "No recent YouTube search" in result


def test_admin_queue_add_youtube_delegates():
    conn = account_conn("bob", "bob_account")
    radio.last_youtube_url[("#chan", "bob")] = "http://yt"
    try:
        with patch(
            "plugins.radio.add_url_to_queue", return_value="ok"
        ) as add_mock:
            result = radio.admin_queue_add_youtube(
                "", MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
            )
        assert result == "ok"
        assert add_mock.call_args.args[1] == "fallback"
    finally:
        radio.last_youtube_url.pop(("#chan", "bob"), None)


def test_admin_queue_add_gse_requires_auth():
    result = radio.admin_queue_add_gse(
        "", MagicMock(), make_bot(RADIO_CFG), MockConn(), "bob", "#chan"
    )
    assert "authenticated" in result


def test_admin_queue_add_gse_no_recent_other_nick():
    conn = account_conn("bob", "bob_account")
    result = radio.admin_queue_add_gse(
        "alice", MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
    )
    assert "found for alice" in result


def test_admin_queue_add_gse_delegates():
    conn = account_conn("bob", "bob_account")
    radio.last_gse_url[("#chan", "bob")] = "http://gse"
    try:
        with patch(
            "plugins.radio.add_url_to_queue", return_value="ok"
        ) as add_mock:
            result = radio.admin_queue_add_gse(
                "", MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
            )
        assert result == "ok"
        assert add_mock.call_args.args[1] == "fallback"
    finally:
        radio.last_gse_url.pop(("#chan", "bob"), None)


# --- random_slop ---


def test_random_slop_rate_limited():
    now = radio.time.time()
    radio.queue_additions_tracker[("#chan", "bob")] = [now] * 10
    conn = MockConn()
    result = radio.random_slop(
        MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
    )
    assert "Rate limit exceeded" in result


def test_random_slop_success_with_metadata(mock_requests):
    mock_requests.add(
        "GET",
        "https://n8n.t3ks.com/webhook/slopradio",
        json={"name": "Song", "artist": "Artist"},
    )
    conn = MockConn()
    event = MagicMock()
    result = radio.random_slop(event, make_bot(RADIO_CFG), conn, "bob", "#chan")
    assert (
        result
        == "✅ Added to queue: Artist - Song | Coming up soon at http://radio.example.com:8000"
    )
    event.reply.assert_called_once_with("⏳ Fetching random Suno song...")


def test_random_slop_success_custom_webhook_and_headers(mock_requests):
    mock_requests.add(
        "GET",
        "https://custom.example.com/hook",
        json={},
        match=[matchers.header_matcher({"X-Token": "secret"})],
    )
    cfg = dict(
        RADIO_CFG,
        n8n_slop={
            "webhook_url": "https://custom.example.com/hook",
            "header_name": "X-Token",
            "header_value": "secret",
        },
    )
    conn = MockConn()
    result = radio.random_slop(MagicMock(), make_bot(cfg), conn, "bob", "#chan")
    assert "Added random Suno song" in result


def test_random_slop_http_error_with_detail(mock_requests):
    mock_requests.add(
        "GET",
        "https://n8n.t3ks.com/webhook/slopradio",
        json={"detail": "no songs"},
        status=400,
    )
    conn = MockConn()
    result = radio.random_slop(
        MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
    )
    assert "no songs" in result


def test_random_slop_http_error_non_json(mock_requests):
    mock_requests.add(
        "GET",
        "https://n8n.t3ks.com/webhook/slopradio",
        body="oops",
        status=503,
        content_type="text/plain",
    )
    conn = MockConn()
    result = radio.random_slop(
        MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
    )
    assert "HTTP 503" in result


def test_random_slop_request_exception(mock_requests):
    mock_requests.add(
        "GET",
        "https://n8n.t3ks.com/webhook/slopradio",
        body=requests.exceptions.ConnectionError("down"),
    )
    conn = MockConn()
    result = radio.random_slop(
        MagicMock(), make_bot(RADIO_CFG), conn, "bob", "#chan"
    )
    assert "Failed to add random song" in result


# --- subscribe_radio_webhook ---


def test_subscribe_radio_webhook_no_bot(unset_bot):
    radio.bot.set(None)
    radio.subscribe_radio_webhook()


def test_subscribe_radio_webhook_not_configured(mock_bot_factory):
    mock_bot_factory(config={"plugins": {"radio": {}}})
    radio.subscribe_radio_webhook()


def test_subscribe_radio_webhook_subscribes(mock_bot_factory):
    mock_bot_factory(
        config={
            "plugins": {"radio": RADIO_CFG},
            "webhooks": {
                "subscriptions": [
                    {"plugin": "radio", "events": ["song_changed"]},
                    {"plugin": "other"},
                    {"plugin": "radio", "events": ["queue_switched"]},
                ]
            },
        }
    )
    try:
        with patch("plugins.radio.httpx.post") as post_mock:
            post_mock.return_value = MagicMock(
                raise_for_status=MagicMock(return_value=None)
            )
            radio.subscribe_radio_webhook()
        assert post_mock.call_count == 2
        from cloudbot.webhooks.handlers import webhook_handlers

        assert webhook_handlers["radio"] is radio.handle_radio_webhook
    finally:
        from cloudbot.webhooks import handlers as webhook_handlers_mod

        webhook_handlers_mod.webhook_handlers.pop("radio", None)


def test_subscribe_radio_webhook_post_error(mock_bot_factory):
    import httpx

    mock_bot_factory(
        config={
            "plugins": {"radio": RADIO_CFG},
            "webhooks": {"subscriptions": [{"plugin": "radio"}]},
        }
    )
    try:
        with patch(
            "plugins.radio.httpx.post",
            side_effect=httpx.HTTPError("boom"),
        ):
            radio.subscribe_radio_webhook()
    finally:
        from cloudbot.webhooks import handlers as webhook_handlers_mod

        webhook_handlers_mod.webhook_handlers.pop("radio", None)
