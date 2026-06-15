import base64
from unittest.mock import MagicMock

import pytest
from irclib.parser import Message, MessageTag

from cloudbot.event import Event
from cloudbot.plugin import PluginManager
from cloudbot.plugin_hooks import CommandHook, CommandInfo
from cloudbot.util.irc import is_channel
from plugins.core import bot_cmds


def make_command_hook(name, aliases=(), doc=None, permissions=()):
    def fn():
        pass

    raw = MagicMock()
    raw.function = fn
    raw.main_alias = name
    raw.aliases = [name, *aliases]
    raw.doc = doc
    raw.kwargs = {"permissions": list(permissions)}
    return CommandHook(MagicMock(), raw)


def pending(**kwargs):
    base = {
        "target": "#chan",
        "invoker": "alice",
        "msgid": "mid",
        "context": "public",
        "cmd_name": "weather",
        "options": {},
    }
    base.update(kwargs)
    return bot_cmds.PendingInvocation(**base)


# --- tag value codec ---


@pytest.mark.parametrize(
    "obj",
    [
        {"msg": "workflow", "id": "abc", "state": "complete"},
        {"a": [1, 2, 3], "b": {"c": "d"}},
        [],
        "string",
    ],
)
def test_codec_roundtrip(obj):
    assert bot_cmds.decode(bot_cmds.encode(obj)) == obj


def test_decode_rejects_bad_input():
    assert bot_cmds.decode(None) is None
    assert bot_cmds.decode("") is None
    assert bot_cmds.decode(base64.b64encode(b"not json {").decode()) is None


def test_fits_under_budget():
    assert bot_cmds.fits_under({"a": 1})
    assert not bot_cmds.fits_under({"a": "x" * (bot_cmds.JSON_BUDGET + 1)})


# --- unified command descriptor ---


def test_command_hook_info():
    hook = make_command_hook(
        "Foo", aliases=["Bar", "Baz"], doc="  does foo  ", permissions=["admin"]
    )
    assert hook.info() == CommandInfo(
        name="foo",
        description="does foo",
        aliases=["bar", "baz"],
        permissions=["admin"],
    )


def test_unique_commands_dedupes_aliases_and_sorts():
    pm = PluginManager(MagicMock())
    foo = make_command_hook("foo", aliases=["f"])
    bar = make_command_hook("bar")
    pm.commands = {"foo": foo, "f": foo, "bar": bar}
    assert pm.unique_commands() == [bar, foo]
    assert [info.name for info in pm.command_infos()] == ["bar", "foo"]


# --- bot-cmds discovery schema ---


def test_build_command_list_filters_truncates_and_falls_back():
    bot = MagicMock()
    bot.plugin_manager.command_infos.return_value = [
        CommandInfo("weather", "Get the weather", ["w", "forecast"], []),
        CommandInfo("_internal", "hidden", [], []),
        CommandInfo("long", "x" * 200, [], []),
        CommandInfo("nodoc", "", [], []),
    ]
    result = bot_cmds.build_command_list(bot, ".")

    assert result["prefix"] == "."
    assert [c["name"] for c in result["commands"]] == [
        "weather",
        "long",
        "nodoc",
    ]
    by_name = {c["name"]: c for c in result["commands"]}
    assert by_name["long"]["description"].endswith("...")
    assert len(by_name["long"]["description"]) <= 100
    assert by_name["nodoc"]["description"] == "(no description)"
    assert by_name["weather"]["aliases"] == ["w", "forecast"]
    assert by_name["weather"]["contexts"] == ["public", "pm"]
    assert by_name["weather"]["options"][0]["name"] == "text"


# --- pending invocation correlation ---


def test_consume_pending_takes_freshest_then_pops():
    conn = MagicMock()
    conn.memory = {}
    older = pending(msgid="m1", ts=1.0)
    newer = pending(msgid="m2", ts=2.0)
    bot_cmds._set_pending(conn, older)
    bot_cmds._set_pending(conn, newer)

    assert bot_cmds._consume_pending_by_target(conn, "#CHAN") is newer
    assert bot_cmds._consume_pending_by_target(conn, "#chan") is older
    assert bot_cmds._consume_pending_by_target(conn, "#chan") is None
    assert bot_cmds._consume_pending_by_target(conn, "#other") is None


def test_reply_tags_public_carry_invoked_by():
    tags = bot_cmds._reply_tags_for_pending(
        pending(
            context="public",
            invoker="alice",
            cmd_name="weather",
            options={"text": "NY"},
        )
    )
    assert tags["+draft/reply"] == "mid"
    assert "+draft/channel-context" not in tags
    assert bot_cmds.decode(tags["+draft/invoked-by"]) == {
        "nick": "alice",
        "name": "weather",
        "options": {"text": "NY"},
    }


def test_reply_tags_private_carry_channel_context_not_invoked_by():
    tags = bot_cmds._reply_tags_for_pending(
        pending(context="private", channel_context="#secret")
    )
    assert tags["+draft/channel-context"] == "#secret"
    assert "+draft/invoked-by" not in tags


def test_reply_tags_workflow_terminal():
    tags = bot_cmds._reply_tags_for_pending(
        pending(context="pm", workflow_id="wf1")
    )
    assert bot_cmds.decode(tags["+draft/bot-tools"]) == {
        "msg": "workflow",
        "id": "wf1",
        "state": "complete",
    }


# --- outbound reply tagging (irc_out) ---


def test_attach_reply_tags_stamps_matching_reply():
    conn = MagicMock()
    conn.memory = {}
    bot_cmds._set_pending(conn, pending(target="#chan", msgid="mid"))
    line = "PRIVMSG #chan :sunny"
    parsed = Message.parse(line)

    result = bot_cmds.attach_reply_tags(parsed, conn, line)

    assert result.tags["+draft/reply"].value == "mid"
    assert "+draft/invoked-by" in result.tags


def test_attach_reply_tags_passthrough_without_pending():
    conn = MagicMock()
    conn.memory = {}
    line = "PRIVMSG #chan :hi"
    assert bot_cmds.attach_reply_tags(Message.parse(line), conn, line) == line


def test_attach_reply_tags_ignores_non_message_commands():
    conn = MagicMock()
    conn.memory = {}
    line = "JOIN #chan"
    assert bot_cmds.attach_reply_tags(Message.parse(line), conn, line) == line


# --- workflow streaming ---


def test_emit_workflow_encodes_payload():
    conn = MagicMock()
    bot_cmds.emit_workflow(
        conn, "#chan", "wf1", "start", name="agent", features=["reasoning"]
    )
    target, tags = conn.tagmsg.call_args.args
    assert target == "#chan"
    assert bot_cmds.decode(tags["+draft/bot-tools"]) == {
        "msg": "workflow",
        "id": "wf1",
        "state": "start",
        "name": "agent",
        "features": ["reasoning"],
    }


def test_emit_step_truncates_oversized_content_to_fit_budget():
    conn = MagicMock()
    bot_cmds.emit_step(
        conn,
        "#chan",
        "wf1",
        "s1",
        "tool-result",
        "complete",
        content="x" * 5000,
    )
    _, tags = conn.tagmsg.call_args.args
    step = bot_cmds.decode(tags["+draft/bot-tools"])
    assert step["truncated"] is True
    assert step["content"].endswith("...")
    assert bot_cmds.fits_under(step)


# --- shared IRC primitives ---


def test_event_tag_value():
    event = Event(
        irc_tags={
            "msgid": MessageTag("msgid", "abc"),
            "bare": MessageTag("bare", None),
        }
    )
    assert event.tag_value("msgid") == "abc"
    assert event.tag_value("bare") is None
    assert event.tag_value("absent") is None
    assert Event().tag_value("anything") is None


def test_is_channel_uses_advertised_chantypes():
    conn = MagicMock()
    conn.memory = {"server_info": {"isupport_tokens": {"CHANTYPES": "#&"}}}
    assert is_channel(conn, "#chan")
    assert is_channel(conn, "&local")
    assert not is_channel(conn, "user")
    assert not is_channel(conn, "")


def test_is_channel_falls_back_without_isupport():
    conn = MagicMock()
    conn.memory = {}
    assert is_channel(conn, "#chan")
    assert not is_channel(conn, "nick")
