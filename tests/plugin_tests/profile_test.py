from unittest.mock import MagicMock

import pytest

from cloudbot.event import CommandEvent
from cloudbot.util.command_args import CommandArgError, build_spec
from plugins import profile
from tests.util import command_hook, wrap_hook_response


def test_profile_add(mock_db, mock_bot):
    profile.table.create(mock_db.engine)
    profile.load_cache(mock_db.session())
    conn = MagicMock()
    event = CommandEvent(
        bot=mock_bot,
        conn=conn,
        hook=command_hook(profile.profileadd),
        nick="nick",
        channel="#chan",
        text="foo bar",
        triggered_command="profileadd",
        cmd_prefix=".",
    )
    event.db = mock_db.session()
    assert event.parse_command_args()
    res = wrap_hook_response(profile.profileadd, event)
    assert res == [
        ("return", "Created new profile category foo"),
    ]
    assert mock_db.get_data(profile.table) == [("#chan", "nick", "foo", "bar")]
    assert conn.mock_calls == []


def test_profile_update(mock_db, mock_bot):
    profile.table.create(mock_db.engine)
    mock_db.add_row(
        profile.table, chan="#chan", nick="nick", category="foo", text="text"
    )
    profile.load_cache(mock_db.session())
    conn = MagicMock()
    event = CommandEvent(
        bot=mock_bot,
        conn=conn,
        hook=command_hook(profile.profileadd),
        nick="nick",
        channel="#chan",
        text="foo bar",
        triggered_command="profileadd",
        cmd_prefix=".",
    )

    event.db = mock_db.session()
    assert event.parse_command_args()
    res = wrap_hook_response(profile.profileadd, event)
    assert res == [
        ("return", "Updated profile category foo"),
    ]
    assert mock_db.get_data(profile.table) == [("#chan", "nick", "foo", "bar")]
    assert conn.mock_calls == []


def test_profile_category_delete(mock_db, mock_bot):
    profile.table.create(mock_db.engine)
    mock_db.add_row(
        profile.table, chan="#chan", nick="nick", category="foo", text="text"
    )
    profile.load_cache(mock_db.session())
    conn = MagicMock()
    event = CommandEvent(
        bot=mock_bot,
        conn=conn,
        hook=command_hook(profile.profiledel),
        nick="nick",
        channel="#chan",
        text="foo",
        triggered_command="profiledel",
        cmd_prefix=".",
    )

    event.db = mock_db.session()
    assert event.parse_command_args()
    res = wrap_hook_response(profile.profiledel, event)
    assert res == [("return", "Deleted profile category foo")]
    assert mock_db.get_data(profile.table) == []
    assert conn.mock_calls == []


def test_profile_clear(mock_db, mock_bot):
    profile.table.create(mock_db.engine)
    mock_db.add_row(
        profile.table, chan="#chan", nick="nick", category="foo", text="text"
    )
    mock_db.add_row(
        profile.table, chan="#chan", nick="nick", category="bar", text="thing"
    )
    profile.load_cache(mock_db.session())
    profile.confirm_keys["#chan"]["nick"] = "foo"
    conn = MagicMock()
    event = CommandEvent(
        bot=mock_bot,
        conn=conn,
        hook=command_hook(profile.profileclear),
        nick="nick",
        channel="#chan",
        text="foo",
        triggered_command="profileclear",
        cmd_prefix=".",
    )

    event.db = mock_db.session()
    assert event.parse_command_args()
    res = wrap_hook_response(profile.profileclear, event)
    assert res == [
        ("return", "Profile data cleared for nick."),
    ]
    assert mock_db.get_data(profile.table) == []
    assert conn.mock_calls == []


def test_profile_add_invalid_category(mock_db, mock_bot):
    profile.table.create(mock_db.engine)
    profile.load_cache(mock_db.session())
    event = CommandEvent(
        bot=mock_bot,
        conn=MagicMock(),
        hook=command_hook(profile.profileadd),
        nick="nick",
        channel="#chan",
        text="foo-bar baz",
        triggered_command="profileadd",
        cmd_prefix=".",
    )
    event.db = mock_db.session()
    assert event.parse_command_args()
    res = wrap_hook_response(profile.profileadd, event)
    assert "Invalid data" in str(res[0].value)
    assert mock_db.get_data(profile.table) == []


@pytest.mark.parametrize(
    "func,text,expected",
    [
        (
            profile.profileadd,
            "foo bar baz",
            {"category": "foo", "content": "bar baz"},
        ),
        (profile.profiledel, "foo", {"category": "foo"}),
        (profile.profileclear, "", {"key": None}),
        (profile.profileclear, "abc", {"key": "abc"}),
    ],
)
def test_typed_args_parse(func, text, expected):
    assert build_spec(func).parse_text(text) == expected


def test_typed_args_reject():
    with pytest.raises(CommandArgError):
        build_spec(profile.profileadd).parse_text("foo")
