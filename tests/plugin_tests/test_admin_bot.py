from unittest.mock import MagicMock, call

import pytest

from cloudbot.event import CommandEvent
from cloudbot.util import async_util
from cloudbot.util.command_args import CommandArgError, build_spec
from plugins import admin_bot
from tests.util import command_hook


@pytest.mark.parametrize(
    "text,chan,out",
    [
        ("foo", "#bar", ["foo"]),
        ("", "#bar", ["#bar"]),
        ("foo baz", "#bar", ["foo", "baz"]),
    ],
)
def test_parse_targets(text, chan, out):
    assert admin_bot.parse_targets(text, chan) == out


@pytest.mark.asyncio
async def test_reload_config(event_loop):
    bot = MagicMock()
    future = event_loop.create_future()
    bot.reload_config.return_value = future
    future.set_result(True)
    res = await admin_bot.rehash_config(bot)
    assert res == "Config reloaded."
    assert bot.mock_calls == [call.reload_config()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_text,chan,key",
    [
        ("#channel key", "#channel", "key"),
        ("channel key", "#channel", "key"),
        ("#channel", "#channel", None),
        ("channel", "#channel", None),
    ],
)
async def test_join(input_text, chan, key, event_loop):
    conn = MagicMock()
    conn.config = {}
    conn.bot = None
    hook = command_hook(admin_bot.join)

    event = CommandEvent(
        text=input_text,
        cmd_prefix=".",
        triggered_command="join",
        hook=hook,
        bot=conn.bot,
        conn=conn,
        channel="#foo",
        nick="foobaruser",
    )

    assert event.parse_command_args()
    await async_util.run_func_with_args(event_loop, admin_bot.join, event)

    event.conn.join.assert_called_with(chan, key)


@pytest.mark.asyncio
async def test_me():
    event = MagicMock()
    conn = MagicMock()

    res = await admin_bot.me(conn, "#foo", "bar", event, action="do thing")
    assert res is None
    assert event.mock_calls == [
        call.admin_log('bar used ME to make me ACT "do thing" in #foo.')
    ]
    assert conn.mock_calls == [call.ctcp("#foo", "ACTION", "do thing")]


@pytest.mark.asyncio
async def test_say_channel():
    conn = MagicMock()
    event = MagicMock()

    await admin_bot.say(
        conn, "#foo", "bar", event, channel="#baz", message="hi"
    )
    conn.message.assert_called_once_with("#baz", "hi")
    event.admin_log.assert_called_once_with(
        'bar used SAY to make me SAY "hi" in #baz.'
    )


@pytest.mark.parametrize(
    "func,text,expected",
    [
        (admin_bot.get_group_users, "admins", {"group": "admins"}),
        (
            admin_bot.remove_permission_user,
            "bob",
            {"user": "bob", "group": None},
        ),
        (
            admin_bot.remove_permission_user,
            "bob admins",
            {"user": "bob", "group": "admins"},
        ),
        (
            admin_bot.add_permissions_user,
            "a!b@c admins",
            {"user": "a!b@c", "group": "admins"},
        ),
        (admin_bot.stop, "", {"reason": None}),
        (admin_bot.restart, "going down now", {"reason": "going down now"}),
        (admin_bot.part, "", {"channel": None}),
        (admin_bot.cycle, "#a #b", {"channel": "#a #b"}),
        (admin_bot.change_nick, "newnick", {"new_nick": "newnick"}),
        (admin_bot.raw, "MODE #a +o b", {"command": "MODE #a +o b"}),
        (admin_bot.say, "hi there", {"channel": None, "message": "hi there"}),
        (
            admin_bot.say,
            "#foo hi there",
            {"channel": "#foo", "message": "hi there"},
        ),
        (
            admin_bot.send_message,
            "bob hi there",
            {"name": "bob", "message": "hi there"},
        ),
        (admin_bot.me, "waves", {"channel": None, "action": "waves"}),
        (admin_bot.me, "#foo waves", {"channel": "#foo", "action": "waves"}),
    ],
)
def test_typed_args_parse(func, text, expected):
    assert build_spec(func).parse_text(text) == expected


@pytest.mark.parametrize(
    "func,text",
    [
        (admin_bot.add_permissions_user, "a!b@c"),
        (admin_bot.send_message, "bob"),
        (admin_bot.say, ""),
    ],
)
def test_typed_args_reject(func, text):
    with pytest.raises(CommandArgError):
        build_spec(func).parse_text(text)
