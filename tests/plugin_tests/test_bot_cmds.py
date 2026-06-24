import base64
import dataclasses
from unittest.mock import MagicMock

import pytest
from irclib.parser import Message, MessageTag

from cloudbot.event import Event
from cloudbot.plugin import PluginManager
from cloudbot.plugin_hooks import CommandHook, CommandInfo
from cloudbot.util.irc import is_channel
from plugins.core import bot_cmds


class _RecordingConn:
    """Records the raw IRC lines a send would put on the wire. The loop queues
    callbacks; call run() to drain them (lets a test inspect in-flight state).
    """

    def __init__(self, max_line_length=510):
        self.lines = []
        self.nick = "TestBot"
        self.config = {"max_line_length": max_line_length}
        self.memory = {}
        self._pending: list[tuple] = []
        conn = self

        class _Loop:
            def call_soon(self, fn, *args):
                conn._pending.append((fn, args))

            def call_later(self, _delay, fn, *args):
                conn._pending.append((fn, args))

        self.loop = _Loop()

    def run(self):
        while self._pending:
            fn, args = self._pending.pop(0)
            fn(*args)

    def cmd(self, command, *params, tags=None):
        self.lines.append(
            str(Message(tags, None, command, list(map(str, params))))
        )

    def tagmsg(self, target, tags=None):
        self.cmd("TAGMSG", target, tags=tags)


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
    base = bot_cmds.PendingInvocation(
        target="#chan",
        invoker="alice",
        msgid="mid",
        context="public",
        cmd_name="weather",
        options={},
    )
    return dataclasses.replace(base, **kwargs)


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
    weather = make_command_hook("weather", aliases=["w"])
    archive = make_command_hook("archive")
    pm.commands = {"weather": weather, "w": weather, "archive": archive}
    assert pm.unique_commands() == [archive, weather]
    assert [info.name for info in pm.command_infos()] == ["archive", "weather"]


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
    commands = result["commands"]
    assert isinstance(commands, list)

    assert result["prefix"] == "."
    assert [c["name"] for c in commands] == ["weather", "long", "nodoc"]
    by_name = {c["name"]: c for c in commands}
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
    assert isinstance(step, dict)
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


# --- bot-cmds discovery transport ---


def _big_command_list(n=400):
    big: dict[str, object] = {
        "prefix": ".",
        "commands": [
            {"name": f"cmd{i}", "description": "x" * 40, "aliases": ["a", "b"]}
            for i in range(n)
        ],
    }
    return big


def test_command_list_batch_streams_within_tag_limits():
    conn = _RecordingConn()
    bot_cmds._send_command_list(conn, "somenick", _big_command_list())
    conn.run()

    fragments = [line for line in conn.lines if line.startswith("@batch=")]
    # message-tags total budget, and obbyircd's +draft/bot-cmds value cap.
    assert all(len(line) <= 8191 for line in conn.lines)
    assert all(
        len(frag) <= bot_cmds._FRAGMENT_BYTES + 200 for frag in fragments
    )
    # big fragments mean few messages, not a per-command flood.
    assert len(fragments) < 30
    assert any(line.startswith("BATCH +") for line in conn.lines)
    assert any(line.startswith("BATCH -") for line in conn.lines)


def test_command_list_skips_concurrent_duplicate_send():
    conn = _RecordingConn()
    bot_cmds._send_command_list(conn, "somenick", _big_command_list())
    bot_cmds._send_command_list(
        conn, "SomeNick", _big_command_list()
    )  # in flight

    assert sum(line.startswith("BATCH +") for line in conn.lines) == 1
    conn.run()
    assert "somenick" not in conn.memory[bot_cmds._SENDING_KEY]


def test_small_command_list_sends_single_tagmsg():
    conn = _RecordingConn()
    bot_cmds._send_command_list(conn, "nick", {"prefix": ".", "commands": []})
    assert len(conn.lines) == 1
    assert conn.lines[0].startswith("@+draft/bot-cmds=")


class _DroppingConn(_RecordingConn):
    """Raises mid-stream like IrcClient.send does once the connection drops."""

    def __init__(self, fail_on=2):
        super().__init__()
        self.fail_on = fail_on
        self.sends = 0

    def tagmsg(self, target, tags=None):
        self.sends += 1
        if self.sends == self.fail_on:
            raise ValueError(
                "Client must be connected to irc server to use send"
            )
        super().tagmsg(target, tags=tags)


def test_command_list_frees_inflight_when_send_fails_midstream():
    conn = _DroppingConn()
    bot_cmds._send_command_list(conn, "somenick", _big_command_list())
    conn.run()

    assert "somenick" not in conn.memory[bot_cmds._SENDING_KEY]


def test_discard_pending_removes_entry_and_tolerates_absent():
    conn = MagicMock()
    conn.memory = {}
    bot_cmds._set_pending(conn, pending(msgid="m1"))
    bot_cmds.discard_pending(conn, "m1")
    assert bot_cmds._consume_pending_by_target(conn, "#chan") is None
    bot_cmds.discard_pending(conn, "absent")
