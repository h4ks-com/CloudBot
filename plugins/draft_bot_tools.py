"""
draft/bot-cmds + draft/bot-tools — IRCv3 spec implementation for CloudBot.

Spec: https://ircv3.net/specs/extensions/bot-tools

What's covered:

* CAP request: message-tags, message-ids (msgid), batch, bot-mode,
  draft/bot-cmds, draft/bot-tools.
* draft/bot-cmds:
    - Build the cmd list from CloudBot's plugin manager.
    - Respond to +draft/bot-cmds-query (direct or channel-broadcast).
    - Accept +draft/bot-cmd invocations on TAGMSG, route them through
      CloudBot's normal PRIVMSG command pipeline by synthesising
      `<prefix><name> <args...>` in the same channel/target the
      invocation arrived on, so every existing CloudBot command
      automatically gains structured invocation.
    - Reply tags (+reply, +draft/channel-context) wired via the out-sieve.
    - +draft/bot-cmd-error on a NOTICE when the invocation is malformed.
* draft/bot-tools:
    - This plugin does NOT auto-stream workflows. The spec's workflow
      surface is opt-in per-command -- only commands that actually run
      multi-step work benefit from streaming, and forcing it on every
      reply would clutter the channel with empty start/complete pairs.
    - Exports helpers `emit_workflow`, `emit_step`, `mark_workflow`
      for the agent plugin (and any future multi-step plugin) to call
      directly. `mark_workflow(conn, invocation_msgid, workflow_id)`
      opts the matching pending invocation in: the bot's reply tags
      then carry the terminal `complete` workflow message alongside
      +reply, per the spec's "the reply SHOULD carry the terminal
      workflow message" guidance.
* legacy translation: advertise the bot's `prefix` so a server that
  implements the legacy bridge can upgrade plain text invocations.

Value encoding: compact JSON -> standard base64 with `=` padding.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid

from cloudbot import hook

logger = logging.getLogger("cloudbot")

CAPS = [
    "message-tags",
    "draft/message-tags-0.2",
    "message-ids",
    "draft/message-ids",
    "batch",
    "bot-mode",
    "draft/bot-cmds",
    "draft/bot-tools",
    "echo-message",
    "server-time",
    "account-tag",
    "labeled-response",
    "standard-replies",
]

# Per-spec: ~3000 bytes of JSON keeps the base64-encoded value under the
# 4094-byte client-tag limit.
JSON_BUDGET = 2400  # base64 expands ~4/3, so chunk wire size ~ 3200 + tag overhead.
                    # Stays under both BUFSIZE (512) -- handled by message-tags --
                    # and the obbyircd pushbot tag-value cap of 4096 chars.

# IRC option types supported. CloudBot commands historically take free
# text; we expose one `text` string option for every command, capturing
# the entire trailing payload.
_DEFAULT_OPTION = {
    "name": "text",
    "type": "string",
    "required": False,
    "description": "Arguments for the command.",
}


# --------------------------------------------------------------------------
# Encoding helpers
# --------------------------------------------------------------------------


def encode(obj) -> str:
    """Compact-JSON-serialise + base64-encode an object per the spec."""
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode(value: str):
    """Base64-decode + JSON-parse a tag value. Returns None on failure."""
    if not value:
        return None
    try:
        raw = base64.b64decode(value, validate=False)
        return json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None


def fits_under(obj, budget: int = JSON_BUDGET) -> bool:
    """True if the compact-JSON encoding fits the per-tag budget."""
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return len(raw) <= budget


# --------------------------------------------------------------------------
# Command-list construction
# --------------------------------------------------------------------------


def _is_invokable_command(cmd_hook) -> bool:
    """Skip pseudo/internal commands that shouldn't be advertised."""
    name = getattr(cmd_hook, "name", "")
    if not name or name.startswith("_"):
        return False
    return True


def _build_command_object(cmd_hook):
    """One command-schema object from a CloudBot CommandHook."""
    description = (cmd_hook.doc or "").strip()
    if len(description) > 100:
        description = description[:97].rstrip() + "..."
    if not description:
        description = "(no description)"
    return {
        "name": cmd_hook.name,
        "description": description,
        # CloudBot commands are typically channel-invoked; allow pm too.
        "contexts": ["public", "pm"],
        "options": [dict(_DEFAULT_OPTION)],
    }


def build_command_list(bot, prefix: str) -> dict:
    """Walk plugin_manager.commands, dedup by main alias, emit the spec object."""
    seen = set()
    commands = []
    for name, cmd_hook in bot.plugin_manager.commands.items():
        if cmd_hook.name in seen:
            continue
        if not _is_invokable_command(cmd_hook):
            continue
        seen.add(cmd_hook.name)
        commands.append(_build_command_object(cmd_hook))
    commands.sort(key=lambda c: c["name"])
    return {"prefix": prefix, "commands": commands}


def _split_for_budget(cmds_obj: dict, budget: int = JSON_BUDGET):
    """If the whole list exceeds budget, slice into chunks that each fit."""
    if fits_under(cmds_obj, budget):
        return [cmds_obj]
    chunks = []
    prefix = cmds_obj.get("prefix")
    current_cmds = []
    for cmd in cmds_obj["commands"]:
        candidate = {"prefix": prefix, "commands": current_cmds + [cmd]} if prefix else {"commands": current_cmds + [cmd]}
        if fits_under(candidate, budget) or not current_cmds:
            current_cmds.append(cmd)
        else:
            chunks.append({"prefix": prefix, "commands": current_cmds} if prefix else {"commands": current_cmds})
            current_cmds = [cmd]
            prefix = None  # only attach prefix once
    if current_cmds:
        chunks.append({"prefix": prefix, "commands": current_cmds} if prefix else {"commands": current_cmds})
    return chunks


# --------------------------------------------------------------------------
# CAP negotiation
# --------------------------------------------------------------------------


def _want_cap(cap: str) -> bool:
    return cap.casefold() in {c.casefold() for c in CAPS}


@hook.on_cap_available(*CAPS)
async def request_cap(conn, cap):
    """Request every CAP the spec depends on (+ a few we benefit from)."""
    _ = conn  # unused
    return True


# --------------------------------------------------------------------------
# Connection startup
# --------------------------------------------------------------------------


def _command_prefix(conn) -> str:
    try:
        return conn.config["connection"]["command_prefix"]
    except Exception:
        return "."


@hook.irc_raw("001")
def set_bot_mode(conn):
    """Mark ourselves as a bot AND make sure we can receive discovery DMs.

    The networks where the obbyircd / obby.world stack runs may set
    +D (privdeaf) and/or +R (regonlymsg) on every new connection via
    set::modes-on-connect, which would silently drop incoming
    +draft/bot-cmds-query TAGMSGs from unauthenticated users. We need
    those queries, so explicitly clear those modes alongside setting +B.
    """
    botnick = conn.config.get("nick", "")
    if botnick:
        conn.cmd("MODE", botnick, "+B-DR")


@hook.irc_raw("JOIN")
def advertise_on_join(conn, event):
    """Nudge existing channel members to re-query our commands.

    The spec's discovery flow is client-driven: on join, a client sends a
    channel `+draft/bot-cmds-query`; bots reply privately. That works for
    clients that join AFTER the bot, but pre-existing channel members
    never know to re-query when a fresh bot arrives. Emitting
    `+draft/bot-cmds-changed` to the channel as we join tells every
    capable client to re-query us, surfacing the bot's commands without
    a manual refresh.
    """
    paramlist = event.irc_paramlist or []
    if not paramlist:
        return
    target = paramlist[0]
    # Only fire for our own joins, not relayed JOINs from other users.
    if not event.nick or event.nick.casefold() != conn.config.get("nick", "").casefold():
        return
    if _channel_target(target):
        _send_tagmsg(conn, target, {"+draft/bot-cmds-changed": None})


# --------------------------------------------------------------------------
# TAGMSG dispatcher
# --------------------------------------------------------------------------


def _tag_value(tags, name):
    """Get a tag value out of CloudBot's parsed tag dict.

    The parsed `tags` is a dict whose values may be raw strings or objects
    with a `.value` attribute (irclib MessageTag). Normalize.
    """
    if not tags or name not in tags:
        return None
    raw = tags[name]
    value = getattr(raw, "value", raw)
    if value is None:
        return ""
    return value


def _is_to_us(conn, target: str) -> bool:
    """True iff the TAGMSG target is the bot's own nick."""
    botnick = conn.config.get("nick", "")
    return target.casefold() == botnick.casefold()


def _channel_target(target: str) -> bool:
    """True iff the target looks like a channel name."""
    return bool(target) and target[0] in "#&^$"


def _send_tagmsg(conn, target: str, tags: dict):
    """Send a TAGMSG with the supplied tag dict to target."""
    conn.cmd("TAGMSG", target, tags=tags)


def _send_command_list(conn, to_nick: str, cmds_obj: dict):
    """Reply to a bot-cmds query by TAGMSGing the asker.

    The command list base64 value goes on `+draft/bot-cmds`. When the list
    is too big for a single TAGMSG we slice into chunks and wrap them in a
    `draft/bot-cmds` batch so the asker reassembles them in order.
    """
    chunks = _split_for_budget(cmds_obj)
    if len(chunks) == 1:
        _send_tagmsg(conn, to_nick, {"+draft/bot-cmds": encode(chunks[0])})
        return
    batch_ref = uuid.uuid4().hex[:8]
    conn.cmd("BATCH", "+" + batch_ref, "draft/bot-cmds", to_nick)
    full = json.dumps(cmds_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    full_b64 = base64.b64encode(full).decode("ascii")
    step = 1500
    asyncio.get_event_loop().call_soon(_drain_fragments, conn, to_nick,
                                       batch_ref, full_b64, step)


def _drain_fragments(conn, to_nick: str, batch_ref: str, full_b64: str, step: int,
                     offset: int = 0):
    if offset >= len(full_b64):
        conn.cmd("BATCH", "-" + batch_ref)
        return
    _send_tagmsg(conn, to_nick,
                 {"batch": batch_ref, "+draft/bot-cmds": full_b64[offset:offset+step]})
    asyncio.get_event_loop().call_later(0.1, _drain_fragments,
                                        conn, to_nick, batch_ref, full_b64,
                                        step, offset + step)


def _send_cmd_error(conn, to_nick: str, code: str, text: str,
                    invocation_msgid: str = None, channel: str = None):
    """+draft/bot-cmd-error on a NOTICE, correlated with the invocation."""
    tags = {"+draft/bot-cmd-error": code}
    if invocation_msgid:
        tags["+reply"] = invocation_msgid
    if channel:
        tags["+draft/channel-context"] = channel
    conn.cmd("NOTICE", to_nick, text, tags=tags)


def _set_pending_invocation(conn, msgid: str, info: dict):
    """Stash invocation info so the out-sieve can attach +reply on the response."""
    store = conn.memory.setdefault("bot_cmds_pending", {})
    store[msgid] = info


def _consume_pending_invocation(conn, channel_or_nick: str, sender_nick: str):
    """Pop the most-recent matching invocation for a channel/nick + invoker.

    CloudBot's command pipeline doesn't natively carry our injected msgid
    through to the response, so we key on (target, invoker_nick) and
    drain the freshest pending entry. There's never more than ~1 in
    flight per (channel, user) tuple in normal use.
    """
    store = conn.memory.get("bot_cmds_pending", {})
    matches = sorted(
        (msgid for msgid, info in store.items()
         if info.get("target", "").casefold() == channel_or_nick.casefold()
         and info.get("invoker", "").casefold() == sender_nick.casefold()),
        key=lambda m: store[m].get("ts", 0),
        reverse=True,
    )
    if matches:
        return store.pop(matches[0])
    return None


def _inject_legacy_invocation(conn, channel: str, invoker_mask: str,
                              prefix_text: str):
    """Replay a synthesised `:nick!user@host PRIVMSG <chan> :<prefix><cmd>...`
    line through CloudBot so the normal command pipeline runs.

    CloudBot's IrcClient.parse_line is the entry point that turns a raw
    line into events and dispatches plugin hooks; reusing it means every
    existing command works for free.
    """
    line = ":%s PRIVMSG %s :%s" % (invoker_mask, channel, prefix_text)
    # The IrcClient instance exposes .parse_line on its protocol; the
    # connection's `data_received`/`handle_message` is the public path
    # used by core_hooks. Fall back gracefully if the API moves.
    handle = getattr(conn, "handle_message", None) or getattr(conn, "_handle_line", None)
    if callable(handle):
        try:
            handle(line)
            return True
        except Exception:
            logger.exception("bot-tools: injecting line failed: %r", line)
    return False


def _dispatch_invocation(conn, event, invocation: dict, msgid: str, raw_target: str = None):
    """Run a +draft/bot-cmd by synthesising a legacy command line.

    Then emit the workflow start + remember the invocation so the
    out-sieve can attach +reply on the response.
    """
    cmd_name = invocation.get("name")
    options = invocation.get("options") or {}
    channel_in_payload = invocation.get("channel")
    target_bot = invocation.get("bot")

    # Disambiguation: if the invocation was on a channel and names a
    # different bot, we ignore it.
    if target_bot and target_bot.casefold() != conn.config.get("nick", "").casefold():
        return

    if not cmd_name or not isinstance(cmd_name, str):
        _send_cmd_error(conn, event.nick, "INVALID_COMMAND",
                        "Missing or invalid command name.",
                        invocation_msgid=msgid, channel=channel_in_payload)
        return

    cmd_hook = conn.bot.plugin_manager.commands.get(cmd_name.casefold())
    if not cmd_hook:
        _send_cmd_error(conn, event.nick, "INVALID_COMMAND",
                        "Unknown command: %s" % cmd_name,
                        invocation_msgid=msgid, channel=channel_in_payload)
        return

    # Determine where to inject: if the invocation arrived on the bot
    # (TAGMSG to botnick) and the payload names a channel, that's the
    # `private` context -> reply gets channel-context. If no channel,
    # `pm` context. Otherwise (TAGMSG to a channel) `public`.
    if raw_target is None:
        raw_target = getattr(event, "target", None) or getattr(event, "chan", None)
    if raw_target and _is_to_us(conn, raw_target):
        if channel_in_payload:
            context = "private"
            inject_target = channel_in_payload
        else:
            context = "pm"
            inject_target = event.nick  # respond directly to invoker
    else:
        context = "public"
        inject_target = raw_target  # the channel

    if context != "pm" and not inject_target:
        _send_cmd_error(conn, event.nick, "BAD_CONTEXT",
                        "Cannot determine reply target.",
                        invocation_msgid=msgid, channel=channel_in_payload)
        return

    # Flatten the options. The default schema is a single `text` option
    # holding the trailing text. If the bot publishes typed options later
    # this needs to honour the schema order.
    args = ""
    if isinstance(options, dict):
        if "text" in options:
            args = str(options["text"])
        else:
            args = " ".join(str(v) for v in options.values() if v not in (None, ""))
    elif isinstance(options, list):
        args = " ".join(str(v) for v in options)

    prefix = _command_prefix(conn)
    line_body = "%s%s%s%s" % (prefix, cmd_name, " " if args else "", args)

    invoker_mask = event.mask if hasattr(event, "mask") and event.mask else event.nick

    pending = {
        "target": inject_target,
        "invoker": event.nick,
        "msgid": msgid,
        "context": context,
        "channel_context": channel_in_payload if context == "private" else None,
        # Filled in by mark_workflow() if a plugin opts in to streaming
        # workflow tags on its reply. Otherwise the reply only carries
        # +reply (+ channel-context) and no +draft/bot-tools terminal.
        "workflow_id": None,
        "ts": time.time(),
    }
    _set_pending_invocation(conn, msgid, pending)

    # Replay the legacy line so CloudBot's existing dispatcher handles it.
    if not _inject_legacy_invocation(conn, inject_target, invoker_mask, line_body):
        # If we can't replay, at least notify the invoker.
        _send_cmd_error(conn, event.nick, "INVALID_COMMAND",
                        "Internal: could not dispatch.",
                        invocation_msgid=msgid, channel=channel_in_payload)


# --------------------------------------------------------------------------
# Main TAGMSG hook
# --------------------------------------------------------------------------


@hook.irc_raw("TAGMSG")
async def handle_tagmsg(conn, event):
    """Route incoming TAGMSGs to the bot-cmds + bot-tools handlers.

    Skip TAGMSGs that arrive as part of a `batch` -- they are chathistory
    replay (server delivering channel history on join). Without this we
    would treat every historical +draft/bot-cmds-query in the replay as
    a live query, fire one reply per query, and drown the connection in
    a burst that trips even the +B-bumped recvq budget.
    """
    tags = event.irc_tags or {}
    if not tags:
        return
    if "batch" in tags:
        return

    paramlist = event.irc_paramlist or []
    if not paramlist:
        return
    target = paramlist[0]

    # Discovery: +draft/bot-cmds-query targeted at us OR at a channel
    # we're in. Spec: every bot in the channel replies privately to the
    # asker.
    if "+draft/bot-cmds-query" in tags:
        if _is_to_us(conn, target) or _channel_target(target):
            prefix = _command_prefix(conn)
            cmds_obj = build_command_list(conn.bot, prefix)
            try:
                _send_command_list(conn, event.nick, cmds_obj)
            except Exception:
                logger.exception("bot-tools: failed to send command list")

    # Invocation: +draft/bot-cmd
    if "+draft/bot-cmd" in tags:
        raw = _tag_value(tags, "+draft/bot-cmd")
        invocation = decode(raw)
        msgid = _tag_value(tags, "msgid") or _tag_value(tags, "draft/msgid") or ""
        if not isinstance(invocation, dict):
            _send_cmd_error(conn, event.nick, "INVALID_OPTIONS",
                            "+draft/bot-cmd payload is not a JSON object.",
                            invocation_msgid=msgid)
            return
        _dispatch_invocation(conn, event, invocation, msgid, raw_target=target)

    # Workflow control: +draft/bot-tools with msg=action targeted at us.
    if "+draft/bot-tools" in tags and _is_to_us(conn, target):
        raw = _tag_value(tags, "+draft/bot-tools")
        msg_obj = decode(raw)
        if isinstance(msg_obj, dict) and msg_obj.get("msg") == "action":
            _handle_action(conn, event, msg_obj)


def _handle_action(conn, event, msg_obj: dict):
    """draft/bot-tools action: cancel, approve, reject, input.

    Plugins that opted into workflow state register a handler in
    conn.memory['bot_tools_actions'][workflow_id]; we look it up and call.
    """
    actions = conn.memory.get("bot_tools_actions") or {}
    target = msg_obj.get("target")
    handler = actions.get(target)
    if handler:
        try:
            handler(event, msg_obj)
        except Exception:
            logger.exception("bot-tools: action handler raised")


# --------------------------------------------------------------------------
# Reply tagging
# --------------------------------------------------------------------------


_REPLY_TAG = "+reply"
_BOT_TOOLS_TAG = "+draft/bot-tools"
_CTX_TAG = "+draft/channel-context"


def _reply_tags_for_pending(pending: dict) -> dict:
    """Tags the out-sieve attaches to the bot's reply.

    Always: +reply pointing at the invocation, +draft/channel-context for
    `private` invocations. Optionally: +draft/bot-tools terminal carrying
    the workflow's `complete` state -- only when a plugin opted in by
    calling mark_workflow() with a workflow_id. For commands that don't
    expose workflow state (the common case), the reply gets only the
    correlation tags.
    """
    tags = {_REPLY_TAG: pending["msgid"]}
    if pending.get("channel_context"):
        tags[_CTX_TAG] = pending["channel_context"]
    if pending.get("workflow_id"):
        terminal = {"msg": "workflow", "id": pending["workflow_id"],
                    "state": "complete"}
        tags[_BOT_TOOLS_TAG] = encode(terminal)
    return tags


def mark_workflow(conn, invocation_msgid: str, workflow_id: str) -> bool:
    """Opt this invocation in to workflow streaming: the reply will carry
    the +draft/bot-tools terminal complete in addition to +reply.

    Plugins that emit workflow start/step TAGMSGs themselves should call
    this so the terminal state-change rides on the final response.
    Returns True if a matching pending invocation was found.
    """
    store = conn.memory.get("bot_cmds_pending", {})
    pending = store.get(invocation_msgid)
    if not pending:
        return False
    pending["workflow_id"] = workflow_id
    return True


@hook.sieve()
async def attach_reply_tags(bot, event, plugin):
    """Pre-execution sieve: stash the running command into event.conn.memory.

    We need the post-side (the reply) for tag-attachment; CloudBot's
    sieve fires before the command runs, and the reply path uses the
    `conn.message` / `conn.notice` helpers that don't auto-look-up our
    pending state. Wrap them.
    """
    _ = bot, plugin
    conn = event.conn
    if conn is None:
        return event

    flag = "_bot_cmds_wrapped"
    if getattr(conn, flag, False):
        return event

    original_cmd = conn.cmd

    def wrapped_cmd(command, *params, tags=None):
        if command.upper() in ("PRIVMSG", "NOTICE") and len(params) >= 1:
            target = params[0]
            sender_nick = event.nick if event and event.nick else ""
            pending = _consume_pending_invocation(conn, target, sender_nick)
            if pending:
                merged = dict(tags) if tags else {}
                for k, v in _reply_tags_for_pending(pending).items():
                    merged.setdefault(k, v)
                tags = merged
        return original_cmd(command, *params, tags=tags)

    conn.cmd = wrapped_cmd
    setattr(conn, flag, True)
    return event


# --------------------------------------------------------------------------
# Public helpers for plugins that want to stream workflow steps
# --------------------------------------------------------------------------


def emit_step(conn, target: str, workflow_id: str, sid: str, step_type: str,
              state: str, *, tool: str = None, label: str = None,
              content=None, truncated: bool = False,
              cancelled_by: str = None):
    """Stream a +draft/bot-tools step. Used by plugins that orchestrate
    multi-step work and want to surface it per the spec.
    """
    step = {
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

    if not fits_under(step):
        if isinstance(step.get("content"), str):
            # Truncate aggressively to fit; callers wanting full fidelity
            # should use the content-stream batch.
            keep = max(0, JSON_BUDGET - len(json.dumps(step, separators=(",", ":")).encode("utf-8")) + len(step["content"]) - 200)
            step["content"] = step["content"][:keep] + "..."
            step["truncated"] = True
    _send_tagmsg(conn, target, {_BOT_TOOLS_TAG: encode(step)})


def emit_workflow(conn, target: str, workflow_id: str, state: str, *,
                  name: str = None, trigger: str = None,
                  features: list = None, cancelled_by: str = None):
    """Stream a +draft/bot-tools workflow state-change."""
    obj = {"msg": "workflow", "id": workflow_id, "state": state}
    if name is not None:
        obj["name"] = name
    if trigger is not None:
        obj["trigger"] = trigger
    if features is not None:
        obj["features"] = features
    if cancelled_by is not None:
        obj["cancelled-by"] = cancelled_by
    _send_tagmsg(conn, target, {_BOT_TOOLS_TAG: encode(obj)})


def emit_cmds_changed(conn, channel: str):
    """Tell clients in a channel that our command list changed.

    Plugins that load/unload subcommands at runtime can call this to
    prompt clients to re-query.
    """
    _send_tagmsg(conn, channel, {"+draft/bot-cmds-changed": None})
