from typing import Annotated, Literal
from unittest.mock import MagicMock

import pytest

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.plugin_hooks import CommandHook
from cloudbot.util import HOOK_ATTR
from cloudbot.util.command_args import (
    Arg,
    ArgSpec,
    CommandArgError,
    CommandSpec,
    build_spec,
    decode_named_args,
    encode_named_args,
)


def ping(
    reply,
    host: Annotated[str, Arg("Host to ping")],
    chan,
    count: Annotated[int, Arg("How many pings")] = 1,
):
    """Ping a host."""


def ask(nick, question: Annotated[str, Arg("What to ask")]):
    pass


def mode(chan, state: Annotated[Literal["enable", "disable"], Arg()]):
    pass


def mixed(
    flag: Annotated[bool, Arg()],
    ratio: Annotated[float, Arg()],
    note: Annotated[str | None, Arg()] = None,
):
    pass


def unannotated(text, chan: Annotated[str, "not an Arg"]):
    pass


def test_spec_keeps_signature_order_and_skips_injected_params():
    spec = build_spec(ping)
    assert spec.args == (
        ArgSpec("host", str, required=True, help="Host to ping"),
        ArgSpec("count", int, required=False, default=1, help="How many pings"),
    )


def test_spec_literal_choices_are_not_rest_of_line():
    (arg,) = build_spec(mode).args
    assert arg.choices == ("enable", "disable")
    assert not arg.rest


def test_spec_last_str_takes_rest_of_line():
    assert build_spec(ask).args[-1].rest
    assert build_spec(mixed).args[-1].rest
    assert not build_spec(ping).args[0].rest


def test_spec_optional_union_unwraps_to_base_type():
    note = build_spec(mixed).args[-1]
    assert note.type is str
    assert not note.required
    assert note.default is None


def test_spec_without_args():
    assert build_spec(unannotated) == CommandSpec(())


@pytest.mark.parametrize(
    "annotation",
    [list[str], int | str, Literal[1, 2], dict],
)
def test_spec_rejects_unsupported_types(annotation):
    def cmd(value: Annotated[annotation, Arg()]):
        pass

    with pytest.raises(TypeError, match="value"):
        build_spec(cmd)


def test_spec_rejects_string_annotation():
    def cmd(value: "Annotated[str, Arg()]"):  # noqa: F821
        pass

    with pytest.raises(TypeError, match="string annotation"):
        build_spec(cmd)


@pytest.mark.parametrize(
    "func,text,expected",
    [
        (ping, "example.com", {"host": "example.com", "count": 1}),
        (ping, "  example.com   3 ", {"host": "example.com", "count": 3}),
        (ask, "what is irc", {"question": "what is irc"}),
        (mode, "ENABLE", {"state": "enable"}),
        (mixed, "yes 0.5", {"flag": True, "ratio": 0.5, "note": None}),
        (mixed, "off 2 a b", {"flag": False, "ratio": 2.0, "note": "a b"}),
    ],
)
def test_parse_text(func, text, expected):
    assert build_spec(func).parse_text(text) == expected


@pytest.mark.parametrize(
    "func,text,error",
    [
        (ping, "", "missing host"),
        (ping, "example.com many", "count must be of type int"),
        (ping, "example.com 3 4", "too many arguments"),
        (mode, "maybe", "state must be one of enable, disable"),
        (mixed, "perhaps 1", "flag must be yes or no"),
    ],
)
def test_parse_text_errors(func, text, error):
    with pytest.raises(CommandArgError, match=error):
        build_spec(func).parse_text(text)


@pytest.mark.parametrize(
    "func,options,expected",
    [
        (ping, {"host": "example.com"}, {"host": "example.com", "count": 1}),
        (ping, {"host": "a", "count": 4}, {"host": "a", "count": 4}),
        (ping, {"host": "a", "count": "4"}, {"host": "a", "count": 4}),
        (ping, {"host": "a", "count": None}, {"host": "a", "count": 1}),
        (ask, {"question": "two words"}, {"question": "two words"}),
        (mode, {"state": "Disable"}, {"state": "disable"}),
        (
            mixed,
            {"flag": True, "ratio": 3, "note": ""},
            {"flag": True, "ratio": 3.0, "note": None},
        ),
    ],
)
def test_parse_named(func, options, expected):
    assert build_spec(func).parse_named(options) == expected


@pytest.mark.parametrize(
    "func,options,error",
    [
        (ping, {}, "missing host"),
        (ping, {"host": "a", "count": True}, "count must be of type int"),
        (ping, {"host": "a", "count": 1.5}, "count must be of type int"),
        (ping, {"host": 5}, "host must be of type string"),
        (ping, {"host": "a", "port": 1}, "unknown options: port"),
        (mode, {"state": "on"}, "state must be one of"),
    ],
)
def test_parse_named_errors(func, options, error):
    with pytest.raises(CommandArgError, match=error):
        build_spec(func).parse_named(options)


def test_usage_line():
    assert build_spec(ping).usage("Ping a host.") == (
        "<host> [count] - Ping a host."
    )
    assert build_spec(mode).usage(None) == "<enable|disable>"


def kick(
    chan,
    *,
    channel: Annotated[str | None, Arg(kind="channel")] = None,
    action: Annotated[Literal["kick", "ban"] | None, Arg()] = None,
    user: Annotated[str, Arg(kind="user")],
    reason: Annotated[str | None, Arg()] = None,
):
    pass


def last(
    user: Annotated[str | None, Arg(rest=True)] = None,
    dontsave: Annotated[Literal["dontsave"] | None, Arg()] = None,
):
    pass


def say(
    message: Annotated[str, Arg(rest=True)],
    loud: Annotated[Literal["loud"] | None, Arg()] = None,
):
    pass


def allow(
    name: Annotated[str, Arg()],
    policy: Annotated[Literal["allow", "deny"] | None, Arg()] = None,
):
    pass


@pytest.mark.parametrize(
    "text,expected",
    [
        ("bob", (None, None, "bob", None)),
        ("#c bob", ("#c", None, "bob", None)),
        ("+c bob", ("+c", None, "bob", None)),
        ("!c BAN bob go away", ("!c", "ban", "bob", "go away")),
        ("Kick bob", (None, "kick", "bob", None)),
        ("bob #c", (None, None, "bob", "#c")),
    ],
)
def test_leading_optional_args_take_only_matching_words(text, expected):
    values = build_spec(kick).parse_text(text)
    assert tuple(values.values()) == expected


def test_leading_channel_follows_server_chantypes():
    spec = build_spec(kick)
    assert spec.parse_text("&c bob", "#")["user"] == "&c"
    assert spec.parse_text("&c bob", "#&")["channel"] == "&c"


def test_leading_optional_args_fill_by_name():
    assert build_spec(kick).parse_named({"user": "bob", "channel": "#c"}) == {
        "channel": "#c",
        "action": None,
        "user": "bob",
        "reason": None,
    }
    assert build_spec(kick).usage(None) == (
        "[channel] [kick|ban] <user> [reason]"
    )


@pytest.mark.parametrize(
    "func,text,expected",
    [
        (
            last,
            "some one DONTSAVE",
            {"user": "some one", "dontsave": "dontsave"},
        ),
        (last, "dontsave", {"user": None, "dontsave": "dontsave"}),
        (last, "dontsave bob", {"user": "dontsave bob", "dontsave": None}),
        (say, "hi there loud", {"message": "hi there", "loud": "loud"}),
        (say, "loud", {"message": "loud", "loud": None}),
    ],
)
def test_trailing_flag_takes_only_a_matching_last_word(func, text, expected):
    assert build_spec(func).parse_text(text) == expected


def test_optional_literal_at_the_end_stays_positional():
    spec = build_spec(allow)
    assert not spec.args[0].rest
    assert spec.parse_text("cmd ALLOW") == {"name": "cmd", "policy": "allow"}
    with pytest.raises(CommandArgError, match="too many"):
        spec.parse_text("cmd allow extra")
    with pytest.raises(CommandArgError, match="policy must be one of"):
        spec.parse_text("cmd maybe")


def test_optional_arg_that_cannot_identify_itself_before_required_is_rejected():
    def cmd(
        *,
        count: Annotated[int, Arg()] = 1,
        host: Annotated[str, Arg()],
    ):
        pass

    with pytest.raises(TypeError, match="count"):
        build_spec(cmd)


def test_rest_arg_must_be_a_str_followed_only_by_optional_literals():
    def not_str(count: Annotated[int, Arg(rest=True)]):
        pass

    def then_required(
        text: Annotated[str, Arg(rest=True)],
        flag: Annotated[Literal["x"], Arg()],
    ):
        pass

    with pytest.raises(TypeError, match="must be a str"):
        build_spec(not_str)
    with pytest.raises(TypeError, match="follow the rest-of-line"):
        build_spec(then_required)


def test_json_schema():
    assert build_spec(kick).json_schema == {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "channel"},
            "action": {
                "type": "string",
                "description": "action",
                "enum": ["kick", "ban"],
            },
            "user": {"type": "string", "description": "user"},
            "reason": {"type": "string", "description": "reason"},
        },
        "required": ["user"],
        "additionalProperties": False,
    }
    assert build_spec(ping).json_schema["properties"]["count"] == {
        "type": "integer",
        "description": "How many pings",
    }


def _command_event(text, named_args=None):
    def cmd(
        reply,
        host: Annotated[str, Arg("Host to ping")],
        count: Annotated[int, Arg("How many pings")] = 1,
    ):
        """Ping a host."""

    hook.command("ping")(cmd)
    cmd_hook = CommandHook(MagicMock(), getattr(cmd, HOOK_ATTR)["command"])
    conn = MagicMock()
    conn.config = {}
    return cmd_hook, CommandEvent(
        hook=cmd_hook,
        text=text,
        triggered_command="ping",
        cmd_prefix=".",
        conn=conn,
        nick="alice",
        named_args=named_args,
    )


def test_command_hook_help_and_injected_args():
    cmd_hook, _ = _command_event("")
    assert cmd_hook.doc == "<host> [count] - Ping a host."
    assert cmd_hook.required_args == ["reply"]
    assert [arg.name for arg in cmd_hook.info().args] == ["host", "count"]


def test_command_event_fills_args_by_name_for_the_call():
    _, event = _command_event("example.com 3")
    assert event.parse_command_args()
    assert (event["host"], event["count"]) == ("example.com", 3)
    assert event["nick"] == "alice"


def test_command_event_prefers_named_args():
    _, event = _command_event("ignored", named_args={"host": "a b"})
    assert event.parse_command_args()
    assert (event["host"], event["count"]) == ("a b", 1)


def test_command_event_notices_usage_on_bad_args():
    _, event = _command_event("example.com lots")
    assert not event.parse_command_args()
    event.conn.notice.assert_called_once_with(
        "alice", ".ping <host> [count] - Ping a host.", tags=None
    )


def test_command_event_reads_chantypes_from_the_connection():
    def cmd(
        *,
        channel: Annotated[str | None, Arg(kind="channel")] = None,
        user: Annotated[str, Arg(kind="user")],
    ):
        """Kick someone."""

    hook.command("kick")(cmd)
    cmd_hook = CommandHook(MagicMock(), getattr(cmd, HOOK_ATTR)["command"])
    conn = MagicMock()
    conn.memory = {"server_info": {"isupport_tokens": {"CHANTYPES": "#"}}}
    event = CommandEvent(
        hook=cmd_hook,
        text="!c bob",
        triggered_command="kick",
        cmd_prefix=".",
        conn=conn,
        nick="alice",
    )
    assert not event.parse_command_args()
    conn.memory = {}
    assert event.parse_command_args()
    assert (event["channel"], event["user"]) == ("!c", "bob")


def test_named_args_codec_roundtrip():
    options = {"host": "a b;c", "count": 2}
    assert decode_named_args(encode_named_args(options)) == options
    assert decode_named_args(None) is None
    with pytest.raises(CommandArgError):
        decode_named_args("[1]")


REPOS = ("Main", "Extra")


def repo_cmd(
    *,
    repo: Annotated[str | None, Arg(choices=REPOS)] = None,
    query: Annotated[str, Arg()],
):
    pass


def test_choices_validate_case_insensitively_and_return_canonical():
    spec = build_spec(repo_cmd)
    assert spec.args[0].choices == REPOS
    assert spec.parse_text("extra foo") == {"repo": "Extra", "query": "foo"}
    assert spec.parse_named({"repo": "MAIN", "query": "q"})["repo"] == "Main"
    with pytest.raises(CommandArgError):
        spec.parse_named({"repo": "other", "query": "q"})


def test_choices_are_self_identifying_leading_optional():
    spec = build_spec(repo_cmd)
    assert spec.parse_text("foo") == {"repo": None, "query": "foo"}
    assert spec.usage(None) == "[Main|Extra] <query>"
    assert spec.json_schema["properties"]["repo"]["enum"] == ["Main", "Extra"]


def test_choices_together_with_literal_is_a_load_error():
    def both(x: Annotated[Literal["a"], Arg(choices=("a",))]):
        pass

    with pytest.raises(TypeError):
        build_spec(both)
