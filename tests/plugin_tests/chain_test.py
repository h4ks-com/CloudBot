from unittest.mock import MagicMock, call

import pytest

from cloudbot import hook
from cloudbot.hook import command
from cloudbot.plugin import Plugin
from cloudbot.plugin_hooks import CommandHook
from cloudbot.util import HOOK_ATTR
from cloudbot.util.command_args import CommandArgError, build_spec
from plugins import chain
from tests.util.mock_bot import MockBot
from tests.util.mock_module import MockModule


def _get_hook(func, name):
    return getattr(func, HOOK_ATTR)[name]


def test_chainlist_empty(mock_db, mock_bot_factory):
    mock_bot = mock_bot_factory(db=mock_db)
    chain.commands.create(mock_db.engine)
    chain.load_cache(mock_db.session())
    event = MagicMock()
    res = chain.chainlist(mock_bot, event)
    assert res is None
    assert event.mock_calls == [call.notice("No commands allowed in 'chain'.")]


def test_chainlist(mock_db, mock_bot_factory):
    mock_bot = mock_bot_factory(db=mock_db)

    @command()
    def func():
        raise NotImplementedError

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        Plugin("test/foo.py", "foo.py", "foo", MockModule()),
        _get_hook(func, "command"),
    )
    db = mock_db.session()
    chain.commands.create(mock_db.engine)
    event = MagicMock()
    mock_db.add_row(chain.commands, hook="foo.func", allowed=True)
    chain.load_cache(db)
    res = chain.chainlist(mock_bot, event)
    assert res is None
    assert event.mock_calls == [call.notice("func")]


@pytest.mark.parametrize(
    "text", ["foo bar", "add", "add some.hook foo", "del", "add a allow extra"]
)
def test_chainallow_rejects_bad_input(text):
    with pytest.raises(CommandArgError):
        build_spec(chain.chainallow).parse_text(text)


def test_chainallow_parses_policy():
    parsed = build_spec(chain.chainallow).parse_text("ADD some.hook Deny")
    assert parsed == {"subcmd": "add", "name": "some.hook", "policy": "deny"}


def test_chainallow_add_no_cmd(mock_db, mock_bot_factory):
    mock_bot = mock_bot_factory(db=mock_db)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "add", "some.hook", "allow")
    assert res == "Unable to find command 'some.hook'"


def test_chainallow_del_no_cmd(mock_db, mock_bot_factory):
    mock_bot = mock_bot_factory(db=mock_db)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "del", "some.hook")
    assert res == "Unable to find command 'some.hook'"


def test_chainallow_del_hook_not_set(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "del", "foo.other_hook_func")
    assert res == "Deleted 0 rows."

    assert mock_db.get_data(chain.commands) == []


def test_chainallow_del_hook(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    mock_db.load_data(
        chain.commands, [{"hook": "foo.other_hook_func", "allowed": False}]
    )

    db = mock_db.session()
    chain.load_cache(db)
    res = chain.chainallow(db, mock_bot, "del", "foo.other_hook_func")
    assert res == "Deleted 1 row."
    assert mock_db.get_data(chain.commands) == []


def test_chainallow_add_hook_no_arg(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "add", "foo.other_hook_func")
    assert res == "Added 'foo.other_hook_func' as an allowed command"

    assert mock_db.get_data(chain.commands) == [
        ("foo.other_hook_func", True),
    ]


def test_chainallow_add_hook_allow(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "add", "foo.other_hook_func", "allow")
    assert res == "Added 'foo.other_hook_func' as an allowed command"

    assert mock_db.get_data(chain.commands) == [
        ("foo.other_hook_func", True),
    ]


def test_chainallow_add_hook_allow_update(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    mock_db.load_data(
        chain.commands, [{"hook": "foo.other_hook_func", "allowed": False}]
    )
    db = mock_db.session()
    chain.load_cache(db)

    res = chain.chainallow(db, mock_bot, "add", "foo.other_hook_func", "allow")
    assert (
        res
        == "Updated state of 'foo.other_hook_func' in chainallow to allowed=True"
    )

    assert mock_db.get_data(chain.commands) == [
        ("foo.other_hook_func", True),
    ]


def test_chainallow_add_hook_deny(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "add", "foo.other_hook_func", "deny")
    assert res == "Added 'foo.other_hook_func' as a denied command"

    assert mock_db.get_data(chain.commands) == [
        ("foo.other_hook_func", False),
    ]


def test_chainallow_add_cmd(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "add", "foo", "allow")
    assert res == "Added 'foo.hook_func' as an allowed command"

    assert mock_db.get_data(chain.commands) == [
        ("foo.hook_func", True),
    ]


def test_chainallow_add_partial_cmd(mock_db, mock_bot_factory):
    @hook.command("foo")
    def hook_func():
        raise NotImplementedError

    @hook.command("bar")
    def other_hook_func():
        raise NotImplementedError

    mock_bot: MockBot = mock_bot_factory(db=mock_db)
    plugin = Plugin("plugins/foo.py", "foo.py", "foo", MockModule())
    mock_bot.plugin_manager.commands["foo"] = CommandHook(
        plugin,
        _get_hook(hook_func, "command"),
    )

    mock_bot.plugin_manager.commands["bar"] = CommandHook(
        plugin,
        _get_hook(other_hook_func, "command"),
    )

    chain.commands.create(mock_db.engine)
    db = mock_db.session()
    res = chain.chainallow(db, mock_bot, "add", "fo", "allow")
    assert res == "Added 'foo.hook_func' as an allowed command"

    assert mock_db.get_data(chain.commands) == [
        ("foo.hook_func", True),
    ]
