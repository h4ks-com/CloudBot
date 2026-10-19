from unittest.mock import MagicMock, call

from cloudbot.util.command_args import build_spec
from plugins import mock as mock_plugin
from tests.util.mock_conn import MockConn


def test_mock_needle_is_optional():
    parsed = build_spec(mock_plugin.mock).parse_text("bar")
    assert parsed == {"target": "bar", "needle": None}


def test_mock_needle_takes_rest_of_line():
    parsed = build_spec(mock_plugin.mock).parse_text("bar hello  there")
    assert parsed == {"target": "bar", "needle": "hello  there"}


def test_mock_no_message():
    conn = MockConn()
    event = MagicMock()
    res = mock_plugin.mock("#foo", conn, event, "bar")
    assert res == "Nothing found in recent history for bar"
    assert event.mock_calls == []


def test_mock_no_matching_message():
    conn = MockConn()
    event = MagicMock()
    chan = "#foo"
    target = "bar"
    conn.history[chan] = [("baz", 123, "Hello this is a test")]
    res = mock_plugin.mock(chan, conn, event, target)
    assert res == "Nothing found in recent history for bar"
    assert event.mock_calls == []


def test_mock():
    conn = MockConn()
    chan = "#foo"
    target = "bar"
    conn.history[chan] = [(target, 123, "Hello this is a test")]
    event = MagicMock()
    res = mock_plugin.mock(chan, conn, event, target)
    assert res is None
    assert event.mock_calls == [call.message("<bar> hElLo tHiS Is a tEsT")]


def test_mock_action():
    conn = MockConn()
    chan = "#foo"
    target = "bar"
    conn.history[chan] = [(target, 123, "\1ACTION Hello this is a test\1")]
    event = MagicMock()
    res = mock_plugin.mock(chan, conn, event, target)
    assert res is None
    assert event.mock_calls == [call.message("* bar hElLo tHiS Is a tEsT")]
