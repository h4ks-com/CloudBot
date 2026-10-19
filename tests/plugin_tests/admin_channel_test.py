from unittest.mock import MagicMock, call

import pytest

from cloudbot.util.command_args import CommandArgError, build_spec
from plugins import admin_channel


def test_ban_no_char():
    event = MagicMock(chan="#bar")
    event.conn.memory = {"server_info": {}}
    admin_channel.ban(event, user="foo")
    assert event.mock_calls == [
        call.notice(
            "Mode character 'b' does not seem to exist on this network."
        )
    ]


def test_ban():
    event = MagicMock(chan="#bar", nick="test")
    event.conn.memory = {"server_info": {"channel_modes": "b"}}
    admin_channel.ban(event, user="foo")
    assert event.mock_calls == [
        call.notice("Attempting to ban foo in #bar..."),
        call.admin_log("test used ban to set +b on foo in #bar."),
        call.conn.send("MODE #bar +b foo"),
    ]


def test_ban_other_chan():
    event = MagicMock(chan="#bar", nick="test")
    event.conn.memory = {"server_info": {"channel_modes": "b"}}
    admin_channel.ban(event, channel="#baz", user="foo")
    assert event.mock_calls == [
        call.notice("Attempting to ban foo in #baz..."),
        call.admin_log("test used ban to set +b on foo in #baz."),
        call.conn.send("MODE #baz +b foo"),
    ]


def test_lock():
    event = MagicMock(chan="#bar", nick="test")
    event.conn.memory = {"server_info": {"channel_modes": "i"}}
    admin_channel.lock(event)
    assert event.mock_calls == [
        call.notice("Attempting to lock #bar..."),
        call.admin_log("test used lock to set +i in #bar."),
        call.conn.send("MODE #bar +i"),
    ]


def test_quiet():
    event = MagicMock(chan="#bar", nick="test")
    event.conn.memory = {
        "server_info": {
            "channel_modes": "b",
            "extbans": "m",
            "extban_prefix": "",
        }
    }
    admin_channel.quiet(event, user="foo")
    assert event.mock_calls == [
        call.notice("Attempting to quiet m:foo in #bar..."),
        call.admin_log("test used quiet to set +b on m:foo in #bar."),
        call.conn.send("MODE #bar +b m:foo"),
    ]


def test_lock_other_chan():
    event = MagicMock(chan="#bar", nick="test")
    event.conn.memory = {"server_info": {"channel_modes": "i"}}
    admin_channel.lock(event, "#other")
    assert event.mock_calls[0] == call.notice("Attempting to lock #other...")


def test_remove_with_reason():
    event = MagicMock()
    conn = MagicMock()
    admin_channel.remove("#bar", conn, "test", event, "bob", "spamming")
    conn.send.assert_called_once_with(
        "REMOVE bob #bar :spamming requested by test"
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("bob", {"channel": None, "user": "bob", "reason": None}),
        (
            "bob being rude",
            {"channel": None, "user": "bob", "reason": "being rude"},
        ),
        ("#baz bob", {"channel": "#baz", "user": "bob", "reason": None}),
        ("&baz bob go", {"channel": "&baz", "user": "bob", "reason": "go"}),
    ],
)
def test_kick_leading_channel(text, expected):
    assert build_spec(admin_channel.kick).parse_text(text) == expected


def test_kick_usage():
    assert build_spec(admin_channel.kick).usage("kicks") == (
        "[channel] <user> [reason] - kicks"
    )


def test_kick_other_chan_with_reason():
    event = MagicMock()
    conn = MagicMock()
    admin_channel.kick(
        "#bar", conn, "test", event, channel="#baz", user="bob", reason="go"
    )
    event.notice.assert_called_once_with("Attempting to kick bob from #baz...")
    conn.send.assert_called_once_with("KICK #baz bob :go")


def test_kick_without_reason():
    conn = MagicMock()
    admin_channel.kick("#bar", conn, "test", MagicMock(), user="bob")
    conn.send.assert_called_once_with("KICK #bar bob")


def test_remove_without_reason():
    event = MagicMock()
    conn = MagicMock()
    admin_channel.remove("#bar", conn, "test", event, "bob")
    conn.send.assert_called_once_with("REMOVE bob #bar :requested by test.")


@pytest.mark.parametrize(
    "command",
    [
        admin_channel.unban,
        admin_channel.quiet,
        admin_channel.unquiet,
        admin_channel.voice,
        admin_channel.devoice,
        admin_channel.op,
        admin_channel.deop,
    ],
)
def test_user_command_parsing(command):
    spec = build_spec(command)
    assert spec.parse_text("bob") == {"channel": None, "user": "bob"}
    assert spec.parse_text("&baz bob") == {"channel": "&baz", "user": "bob"}
    with pytest.raises(CommandArgError):
        spec.parse_text("")


def test_unquiet_other_chan():
    event = MagicMock(chan="#bar", nick="test")
    event.conn.memory = {"server_info": {"channel_modes": "q"}}
    admin_channel.unquiet(event, channel="#baz", user="foo")
    assert event.mock_calls[-1] == call.conn.send("MODE #baz -q foo")


def test_quiet_falls_back_to_extban_in_other_chan():
    event = MagicMock(chan="#bar", nick="test")
    event.conn.memory = {
        "server_info": {
            "channel_modes": "b",
            "extbans": "m",
            "extban_prefix": "~",
        }
    }
    admin_channel.quiet(event, channel="#baz", user="foo")
    assert event.mock_calls[-1] == call.conn.send("MODE #baz +b ~m:foo")


def test_topic_parsing():
    spec = build_spec(admin_channel.topic)
    assert spec.parse_text("hello there") == {
        "channel": None,
        "topic": "hello there",
    }
    assert spec.parse_text("#baz hello there") == {
        "channel": "#baz",
        "topic": "hello there",
    }
    with pytest.raises(CommandArgError):
        spec.parse_text("#baz")


def test_topic_other_chan():
    event = MagicMock()
    conn = MagicMock()
    admin_channel.topic(conn, "#bar", "test", event, channel="#baz", topic="hi")
    conn.send.assert_called_once_with("TOPIC #baz :hi")


def test_topic_current_chan():
    conn = MagicMock()
    admin_channel.topic(conn, "#bar", "test", MagicMock(), topic="hi all")
    conn.send.assert_called_once_with("TOPIC #bar :hi all")
