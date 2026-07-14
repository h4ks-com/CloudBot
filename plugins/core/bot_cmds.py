"""IRCv3 draft/bot-cmds + draft/bot-tools for CloudBot.

Spec: ObbyIRCd/doc/specs/pushbot-spec.md (the obby.world pushbot stack).

draft/bot-cmds turns every existing CloudBot command into a structured,
discoverable slash command: clients query the bot, get a JSON schema of its
commands, and invoke them via +draft/bot-cmd TAGMSG. Invocations are replayed
through the normal PRIVMSG command pipeline so no command needs to know it was
called structurally.

draft/bot-tools layers agentic transparency on top: emit_workflow/emit_step let
a multi-step plugin (the agent) stream its tool calls, and mark_workflow rides
the terminal state on the invocation's reply tags.
"""

import base64
import json
import time
import uuid
from dataclasses import dataclass, field

from irclib.parser import Message

from cloudbot import hook
from cloudbot.hook import Priority
from cloudbot.plugin_hooks import CommandInfo
from cloudbot.util import async_util
from cloudbot.util.irc import is_channel

CAPS = [
    "message-tags",
    "draft/message-tags-0.2",
    "message-ids",
    "draft/message-ids",
    "batch",
    "draft/multiline",
    "bot-mode",
    "draft/bot-cmds",
    "draft/bot-tools",
    "echo-message",
    "server-time",
    "account-tag",
    "labeled-response",
    "standard-replies",
    # Manage our own backlog so servers don't auto-replay history as live messages.
    "draft/chathistory",
    "chathistory",
]

_REPLY_TAG = "+draft/reply"
_CTX_TAG = "+draft/channel-context"
_BOT_TOOLS_TAG = "+draft/bot-tools"
_INVOKED_BY_TAG = "+draft/invoked-by"
_PENDING_KEY = "bot_cmds_pending"
_SENDING_KEY = "bot_cmds_sending"

# obbyircd caps the +draft/bot-cmds tag value at 4096 chars; keep fragments
# under it so a big command list streams in a handful of messages, not a flood.
_FRAGMENT_BYTES = 3500

# obbyircd caps a +draft/bot-tools tag value at 4094 chars and base64 expands the
# payload by ~4/3, so the compact JSON must stay under this to fit in one tag.
JSON_BUDGET = 2400

# CloudBot commands take free text, so every command exposes one `text` option
# capturing the whole trailing payload.
_DEFAULT_OPTION = {
    "name": "text",
    "type": "string",
    "required": False,
    "description": "Arguments for the command.",
}


# --------------------------------------------------------------------------
# Tag value codec: compact JSON over base64, per the spec
# --------------------------------------------------------------------------


def encode(obj: object) -> str:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return base64.b64encode(raw).decode("ascii")


def decode(value: str | None) -> object | None:
    if not value:
        return None
    try:
        raw = base64.b64decode(value, validate=False)
        parsed: object = json.loads(raw.decode("utf-8"))
        return parsed
    except (ValueError, json.JSONDecodeError):
        return None


def fits_under(obj: object, budget: int = JSON_BUDGET) -> bool:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return len(raw) <= budget


@dataclass
class PendingInvocation:
    target: str
    invoker: str
    msgid: str
    context: str
    cmd_name: str
    options: dict[str, object]
    channel_context: str | None = None
    workflow_id: str | None = None
    ts: float = field(default_factory=time.time)


# --------------------------------------------------------------------------
# Command-list construction
# --------------------------------------------------------------------------


def _build_command_object(info: CommandInfo) -> dict[str, object]:
    description = info.description
    if len(description) > 100:
        description = description[:97].rstrip() + "..."
    return {
        "name": info.name,
        "description": description or "(no description)",
        "aliases": info.aliases,
        "contexts": ["public", "pm"],
        "options": [dict(_DEFAULT_OPTION)],
    }


def build_command_list(bot, prefix: str) -> dict[str, object]:
    commands = [
        _build_command_object(info)
        for info in bot.plugin_manager.command_infos()
        if not info.name.startswith("_")
    ]
    return {"prefix": prefix, "commands": commands}


# --------------------------------------------------------------------------
# Connection startup
# --------------------------------------------------------------------------


@hook.on_cap_available(*CAPS)
async def request_cap():
    return True


def _command_prefix(conn) -> str:
    return conn.config.get("command_prefix", ".")


def _is_to_us(conn, target: str) -> bool:
    return target.casefold() == (conn.nick or "").casefold()


@hook.irc_raw("001")
def set_bot_mode(conn):
    """Mark ourselves a bot, and clear +D/+R so discovery DMs from
    unauthenticated users still reach us (some obby.world networks set those
    via set::modes-on-connect, which would drop +draft/bot-cmds-query)."""
    botnick = conn.config.get("nick", "")
    if botnick:
        conn.cmd("MODE", botnick, "+B-DR")


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _send_command_list(conn, to_nick: str, cmds_obj: dict[str, object]) -> None:
    """Reply to a query. A small list fits one TAGMSG; a large one streams across
    a draft/bot-cmds batch the asker concatenates then decodes. Only one stream
    runs per asker at a time so a duplicate query doesn't double the burst."""
    if fits_under(cmds_obj):
        conn.tagmsg(to_nick, {"+draft/bot-cmds": encode(cmds_obj)})
        return
    in_flight = conn.memory.setdefault(_SENDING_KEY, set())
    if to_nick.casefold() in in_flight:
        return
    in_flight.add(to_nick.casefold())
    batch_ref = uuid.uuid4().hex[:8]
    conn.cmd("BATCH", "+" + batch_ref, "draft/bot-cmds", to_nick)
    conn.loop.call_soon(
        _drain_fragments, conn, to_nick, batch_ref, encode(cmds_obj), 0
    )


def _drain_fragments(
    conn, to_nick: str, batch_ref: str, full_b64: str, offset: int
) -> None:
    if offset >= len(full_b64):
        conn.cmd("BATCH", "-" + batch_ref)
        conn.memory.get(_SENDING_KEY, set()).discard(to_nick.casefold())
        return
    try:
        conn.tagmsg(
            to_nick,
            {
                "batch": batch_ref,
                "+draft/bot-cmds": full_b64[offset : offset + _FRAGMENT_BYTES],
            },
        )
    except ValueError:
        # Connection dropped mid-stream: free the in-flight slot so the next
        # query from this nick isn't blocked forever by the dedup guard.
        conn.memory.get(_SENDING_KEY, set()).discard(to_nick.casefold())
        return
    conn.loop.call_later(
        0.1,
        _drain_fragments,
        conn,
        to_nick,
        batch_ref,
        full_b64,
        offset + _FRAGMENT_BYTES,
    )


def _send_cmd_error(
    conn,
    to_nick: str,
    code: str,
    text: str,
    invocation_msgid: str | None = None,
    channel: str | None = None,
) -> None:
    tags: dict[str, str | None] = {"+draft/bot-cmd-error": code}
    if invocation_msgid:
        tags["+reply"] = invocation_msgid
    if channel:
        tags[_CTX_TAG] = channel
    conn.cmd("NOTICE", to_nick, text, tags=tags)


# --------------------------------------------------------------------------
# Invocation
# --------------------------------------------------------------------------


def _set_pending(conn, pending: PendingInvocation) -> None:
    conn.memory.setdefault(_PENDING_KEY, {})[pending.msgid] = pending


def _consume_pending_by_target(conn, target: str) -> PendingInvocation | None:
    """Pop the freshest pending invocation aimed at this target. The reply path
    can't carry the original msgid through, so we correlate on target alone."""
    store: dict[str, PendingInvocation] = conn.memory.get(_PENDING_KEY, {})
    matches = sorted(
        (
            msgid
            for msgid, pending in store.items()
            if pending.target.casefold() == target.casefold()
        ),
        key=lambda m: store[m].ts,
        reverse=True,
    )
    if not matches:
        return None
    return store.pop(matches[0])


def discard_pending(conn, msgid: str) -> None:
    """Drop a pending invocation so no terminal reply-tag rides its next reply."""
    conn.memory.get(_PENDING_KEY, {}).pop(msgid, None)


def _inject_legacy_invocation(
    conn, channel: str, invoker_mask: str, prefix_text: str
) -> bool:
    """Replay a synthesised PRIVMSG so CloudBot's normal command pipeline runs."""
    protocol = conn._protocol
    if protocol is None:
        return False
    event = protocol.parse_line(
        f":{invoker_mask} PRIVMSG {channel} :{prefix_text}"
    )
    async_util.wrap_future(conn.bot.process(event), loop=conn.loop)
    return True


def _dispatch_invocation(
    conn,
    event,
    invocation: dict[str, object],
    msgid: str,
    raw_target: str | None = None,
) -> None:
    cmd_name = invocation.get("name")
    raw_options = invocation.get("options") or {}
    options: dict[str, object] = (
        raw_options if isinstance(raw_options, dict) else {}
    )
    raw_channel = invocation.get("channel")
    channel = raw_channel if isinstance(raw_channel, str) else None
    target_bot = invocation.get("bot")

    if (
        isinstance(target_bot, str)
        and target_bot.casefold() != (conn.config.get("nick") or "").casefold()
    ):
        return

    if not isinstance(cmd_name, str) or not cmd_name:
        _send_cmd_error(
            conn,
            event.nick,
            "INVALID_COMMAND",
            "Missing or invalid command name.",
            invocation_msgid=msgid,
            channel=channel,
        )
        return

    if not conn.bot.plugin_manager.commands.get(cmd_name.casefold()):
        _send_cmd_error(
            conn,
            event.nick,
            "INVALID_COMMAND",
            f"Unknown command: {cmd_name}",
            invocation_msgid=msgid,
            channel=channel,
        )
        return

    if raw_target is None:
        raw_target = event.chan
    if raw_target and _is_to_us(conn, raw_target):
        if channel:
            context = "private"
            inject_target = channel
        else:
            context = "pm"
            inject_target = event.nick
    else:
        context = "public"
        inject_target = raw_target or ""

    if context != "pm" and not inject_target:
        _send_cmd_error(
            conn,
            event.nick,
            "BAD_CONTEXT",
            "Cannot determine reply target.",
            invocation_msgid=msgid,
            channel=channel,
        )
        return

    if "text" in options:
        args = str(options["text"])
    else:
        args = " ".join(str(v) for v in options.values() if v not in (None, ""))

    pending = PendingInvocation(
        target=inject_target,
        invoker=event.nick,
        msgid=msgid,
        context=context,
        cmd_name=cmd_name,
        options=options,
        channel_context=channel if context == "private" else None,
    )
    _set_pending(conn, pending)

    line_body = f"{_command_prefix(conn)}{cmd_name}{' ' if args else ''}{args}"
    if not _inject_legacy_invocation(
        conn, inject_target, event.mask or event.nick, line_body
    ):
        _send_cmd_error(
            conn,
            event.nick,
            "INVALID_COMMAND",
            "Internal: could not dispatch.",
            invocation_msgid=msgid,
            channel=channel,
        )


@hook.irc_raw("TAGMSG")
async def handle_tagmsg(conn, event):
    """Route incoming TAGMSGs to the bot-cmds/bot-tools handlers. TAGMSGs inside
    a batch are chathistory replay and must not be re-answered as live queries.
    """
    if event.nick and event.nick.casefold() == conn.nick.casefold():
        return  # our own TAGMSG echoed back via echo-message
    tags = event.irc_tags or {}
    if not tags or "batch" in tags:
        return
    paramlist = event.irc_paramlist or []
    if not paramlist:
        return
    target = paramlist[0]

    if "+draft/bot-cmds-query" in tags and (
        _is_to_us(conn, target) or is_channel(conn, target)
    ):
        _send_command_list(
            conn,
            event.nick,
            build_command_list(conn.bot, _command_prefix(conn)),
        )

    if "+draft/bot-cmd" in tags:
        invocation = decode(event.tag_value("+draft/bot-cmd"))
        msgid = event.tag_value("msgid") or event.tag_value("draft/msgid") or ""
        if not isinstance(invocation, dict):
            _send_cmd_error(
                conn,
                event.nick,
                "INVALID_OPTIONS",
                "+draft/bot-cmd payload is not a JSON object.",
                invocation_msgid=msgid,
            )
            return
        _dispatch_invocation(conn, event, invocation, msgid, raw_target=target)

    if "+draft/bot-tools" in tags and _is_to_us(conn, target):
        msg_obj = decode(event.tag_value("+draft/bot-tools"))
        if isinstance(msg_obj, dict) and msg_obj.get("msg") == "action":
            _handle_action(conn, event, msg_obj)


# --------------------------------------------------------------------------
# Reply tagging (outbound)
# --------------------------------------------------------------------------


def _reply_tags_for_pending(pending: PendingInvocation) -> dict[str, str]:
    tags = {_REPLY_TAG: pending.msgid}
    if pending.channel_context:
        tags[_CTX_TAG] = pending.channel_context
    if pending.workflow_id:
        tags[_BOT_TOOLS_TAG] = encode(
            {"msg": "workflow", "id": pending.workflow_id, "state": "complete"}
        )
    if pending.context == "public":
        tags[_INVOKED_BY_TAG] = encode(
            {
                "nick": pending.invoker,
                "name": pending.cmd_name,
                "options": pending.options,
            }
        )
    return tags


@hook.irc_out(priority=Priority.HIGHEST)
def attach_reply_tags(parsed_line, conn, line):
    """Stamp +reply/+invoked-by/workflow tags onto the bot's reply to a
    structured invocation, correlating it back to the original +draft/bot-cmd.
    """
    if (
        parsed_line is None
        or parsed_line.command not in ("PRIVMSG", "NOTICE")
        or not parsed_line.parameters
    ):
        return line
    pending = _consume_pending_by_target(conn, parsed_line.parameters[0])
    if pending is None:
        return line
    tags = {name: tag.value for name, tag in (parsed_line.tags or {}).items()}
    for name, value in _reply_tags_for_pending(pending).items():
        tags.setdefault(name, value)
    return Message(
        tags, parsed_line.prefix, parsed_line.command, parsed_line.parameters
    )


# --------------------------------------------------------------------------
# draft/bot-tools workflow streaming (called by multi-step plugins)
# --------------------------------------------------------------------------


def mark_workflow(conn, invocation_msgid: str, workflow_id: str) -> bool:
    """Opt a pending invocation in to workflow streaming so its reply carries the
    terminal state. Returns True if the invocation was still pending."""
    pending = conn.memory.get(_PENDING_KEY, {}).get(invocation_msgid)
    if pending is None:
        return False
    pending.workflow_id = workflow_id
    return True


def emit_step(
    conn,
    target: str,
    workflow_id: str,
    sid: str,
    step_type: str,
    state: str,
    *,
    tool: str | None = None,
    label: str | None = None,
    content: object = None,
    truncated: bool = False,
    cancelled_by: str | None = None,
) -> None:
    step: dict[str, object] = {
        "msg": "step",
        "wid": workflow_id,
        "sid": sid,
        "type": step_type,
        "state": state,
    }
    if tool is not None:
        step["tool"] = tool
    if label is not None:
        step["label"] = label
    if content is not None:
        step["content"] = content
    if truncated:
        step["truncated"] = True
    if cancelled_by is not None:
        step["cancelled-by"] = cancelled_by

    content_val = step.get("content")
    if isinstance(content_val, str) and not fits_under(step):
        step["truncated"] = True
        overhead = len(
            json.dumps(step, separators=(",", ":")).encode("utf-8")
        ) - len(content_val.encode("utf-8"))
        keep = max(0, JSON_BUDGET - overhead - 3)
        step["content"] = (
            content_val.encode("utf-8")[:keep].decode("utf-8", "ignore") + "..."
        )
    conn.tagmsg(target, {_BOT_TOOLS_TAG: encode(step)})


def emit_workflow(
    conn,
    target: str,
    workflow_id: str,
    state: str,
    *,
    name: str | None = None,
    trigger: str | None = None,
    features: list[str] | None = None,
    cancelled_by: str | None = None,
) -> None:
    obj: dict[str, object] = {
        "msg": "workflow",
        "id": workflow_id,
        "state": state,
    }
    if name is not None:
        obj["name"] = name
    if trigger is not None:
        obj["trigger"] = trigger
    if features is not None:
        obj["features"] = features
    if cancelled_by is not None:
        obj["cancelled-by"] = cancelled_by
    conn.tagmsg(target, {_BOT_TOOLS_TAG: encode(obj)})


def start_tool_workflow(
    conn, target: str, name: str, trigger: str | None = None
) -> str:
    """Open a draft/bot-tools workflow for a sub-agent run and return its id. The
    sub-agent's tool calls stream into it via tool_step_sink; the run's final
    message carries workflow_terminal_tag to close the card in place. Emit failures
    (a closed connection) are swallowed so transparency never breaks the run."""
    workflow_id = uuid.uuid4().hex[:12]
    try:
        emit_workflow(
            conn,
            target,
            workflow_id,
            "start",
            name=name,
            trigger=trigger,
            features=["reasoning"],
        )
    except ValueError:
        pass
    return workflow_id


def tool_step_sink(conn, target: str, workflow_id: str):
    """Build the callback for run_subagent(on_tool_step=...) that fans each
    sub-agent tool call out as a workflow step under workflow_id."""

    def sink(
        sid: str, step_type: str, state: str, tool: str, content: object = None
    ) -> None:
        try:
            emit_step(
                conn,
                target,
                workflow_id,
                sid,
                step_type,
                state,
                tool=tool,
                content=content,
            )
        except ValueError:
            pass

    return sink


def workflow_terminal_tag(
    workflow_id: str, state: str
) -> dict[str, str | None]:
    """The +draft/bot-tools tag that closes a workflow in a given state, stamped
    onto a sub-agent's final message so its card morphs into that reply in place.
    """
    return {
        _BOT_TOOLS_TAG: encode(
            {"msg": "workflow", "id": workflow_id, "state": state}
        )
    }


def _handle_action(conn, event, msg_obj: dict) -> None:
    handler = (conn.memory.get("bot_tools_actions") or {}).get(
        msg_obj.get("target")
    )
    if handler is not None:
        handler(event, msg_obj)
