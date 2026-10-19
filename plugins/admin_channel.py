from typing import Annotated

from cloudbot import hook
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg, OptionalChannel

# Messages
NO_MODE = "Mode character {char!r} does not seem to exist on this network."
MODE_CMD_LOG = "{nick} used {cmd} to set {mode} on {target} in {channel}."
MODE_CMD_NO_TARGET_LOG = "{nick} used {cmd} to set {mode} in {channel}."
TOPIC_CHANGE = "{nick} used TOPIC to set the topic in {channel} to {text}."
KICK_LOG = "{nick} used KICK to kick {target} in {channel}."
REMOVE_LOG = "{nick} used REMOVE on {target} in {channel} because {reason!r}."


def check_for_chan_mode(char, mode_warn, event):
    serv_info = event.conn.memory["server_info"]
    modes = serv_info.get("channel_modes", "")
    status = serv_info.get("statuses", [])
    if char in modes or char in status:
        return True

    if mode_warn:
        event.notice(NO_MODE.format(char=char))

    return False


def set_mode(mode, action, channel, target, event, mode_warn=True):
    if not check_for_chan_mode(mode[1], mode_warn, event):
        return False

    event.notice(f"Attempting to {action} {target} in {channel}...")
    event.admin_log(
        MODE_CMD_LOG.format(
            nick=event.nick,
            cmd=action,
            mode=mode,
            target=target,
            channel=channel,
        )
    )
    event.conn.send(f"MODE {channel} {mode} {target}")

    return True


def mode_cmd_no_target(mode, action, param, event, mode_warn=True):
    """generic mode setting function without a target"""
    chan = event.chan
    if not check_for_chan_mode(mode[1], mode_warn, event):
        return False

    split = param.split(" ")
    if split[0].startswith("#"):
        channel = split[0]
    else:
        channel = chan

    event.notice(f"Attempting to {action} {channel}...")
    event.admin_log(
        MODE_CMD_NO_TARGET_LOG.format(
            nick=event.nick,
            cmd=action,
            mode=mode,
            channel=channel,
        )
    )
    event.conn.send(f"MODE {channel} {mode}")
    return True


def do_extban(char, action, channel, target, event, adding=True):
    serv_info = event.conn.memory["server_info"]
    if char not in serv_info.get("extbans", ""):
        return False

    extban_pfx = serv_info["extban_prefix"]
    set_mode(
        "+b" if adding else "-b",
        action,
        channel,
        f"{extban_pfx}{char}:{target}",
        event,
    )
    return True


@hook.command(permissions=["op_ban", "op", "chanop"])
def ban(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to ban", kind="user")],
):
    """bans <user> in [channel], or in the caller's channel if no channel is specified"""
    set_mode("+b", "ban", channel or event.chan, user, event)


@hook.command(permissions=["op_ban", "op", "chanop"])
def unban(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to unban", kind="user")],
):
    """unbans <user> in [channel], or in the caller's channel if no channel is specified"""
    set_mode("-b", "unban", channel or event.chan, user, event)


@hook.command(permissions=["op_quiet", "op", "chanop"])
def quiet(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to quiet", kind="user")],
):
    """quiets <user> in [channel], or in the caller's channel if no channel is specified"""
    channel = channel or event.chan
    if set_mode("+q", "quiet", channel, user, event, False):
        return

    if not do_extban("m", "quiet", channel, user, event, True):
        event.notice("Unable to set +q or a mute extban on this network.")


@hook.command(permissions=["op_quiet", "op", "chanop"])
def unquiet(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to unquiet", kind="user")],
):
    """unquiets <user> in [channel], or in the caller's channel if no channel is specified"""
    channel = channel or event.chan
    if set_mode("-q", "unquiet", channel, user, event, False):
        return

    if not do_extban("m", "unquiet", channel, user, event, False):
        event.notice("Unable to unset +q or a mute extban on this network.")


@hook.command(permissions=["op_voice", "op", "chanop"])
def voice(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to voice", kind="user")],
):
    """voices <user> in [channel], or in the caller's channel if no channel is specified"""
    set_mode("+v", "voice", channel or event.chan, user, event)


@hook.command(permissions=["op_voice", "op", "chanop"])
def devoice(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to devoice", kind="user")],
):
    """devoices <user> in [channel], or in the caller's channel if no channel is specified"""
    set_mode("-v", "devoice", channel or event.chan, user, event)


@hook.command(permissions=["op_op", "op", "chanop"])
def op(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to op", kind="user")],
):
    """ops <user> in [channel], or in the caller's channel if no channel is specified"""
    set_mode("+o", "op", channel or event.chan, user, event)


@hook.command(permissions=["op_op", "op", "chanop"])
def deop(
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to deop", kind="user")],
):
    """deops <user> in [channel], or in the caller's channel if no channel is specified"""
    set_mode("-o", "deop", channel or event.chan, user, event)


@hook.command(permissions=["op_topic", "op", "chanop"])
def topic(
    conn: IrcClient,
    chan: str,
    nick: str,
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    topic: Annotated[str, Arg("New topic")],
):
    """changes the topic to <topic> in [channel], or in the caller's channel
    if no channel is specified"""
    channel = channel or chan
    event.admin_log(TOPIC_CHANGE.format(nick=nick, channel=channel, text=topic))
    conn.send(f"TOPIC {channel} :{topic}")


@hook.command(permissions=["op_kick", "op", "chanop"])
def kick(
    chan: str,
    conn: IrcClient,
    nick: str,
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    user: Annotated[str, Arg("Nick to kick", kind="user")],
    reason: Annotated[str | None, Arg("Why they are kicked")] = None,
):
    """kicks <user> from [channel], or from the caller's channel if no channel is specified"""
    channel = channel or chan
    out = f"KICK {channel} {user}"
    if reason:
        out += f" :{reason}"

    event.notice(f"Attempting to kick {user} from {channel}...")
    event.admin_log(KICK_LOG.format(nick=nick, target=user, channel=channel))
    conn.send(out)


@hook.command(permissions=["op_rem", "op", "chanop"])
def remove(
    chan: str,
    conn: IrcClient,
    nick: str,
    event: CommandEvent,
    user: Annotated[str, Arg("Nick to remove", kind="user")],
    reason: Annotated[str | None, Arg("Why they are removed")] = None,
):
    """force removes the user from the caller's channel."""
    if reason:
        reason = f"{reason} requested by {nick}"
    else:
        reason = f"requested by {nick}."
    out = f"REMOVE {user} {chan} :{reason}"
    event.admin_log(
        REMOVE_LOG.format(nick=nick, target=user, channel=chan, reason=reason)
    )
    conn.send(out)


@hook.command(permissions=["op_mute", "op", "chanop"], autohelp=False)
def mute(
    event: CommandEvent,
    channel: OptionalChannel = None,
):
    """mutes the channel, or in the caller's channel if no channel is specified"""
    mode_cmd_no_target("+m", "mute", channel or "", event)


@hook.command(permissions=["op_mute", "op", "chanop"], autohelp=False)
def unmute(
    event: CommandEvent,
    channel: OptionalChannel = None,
):
    """unmutes the channel, or in the caller's channel if no channel is specified"""
    mode_cmd_no_target("-m", "unmute", channel or "", event)


@hook.command(permissions=["op_lock", "op", "chanop"], autohelp=False)
def lock(
    event: CommandEvent,
    channel: OptionalChannel = None,
):
    """locks the channel, or in the caller's channel if no channel is specified"""
    mode_cmd_no_target("+i", "lock", channel or "", event)


@hook.command(permissions=["op_lock", "op", "chanop"], autohelp=False)
def unlock(
    event: CommandEvent,
    channel: OptionalChannel = None,
):
    """unlocks the channel, or in the caller's channel if no channel is specified"""
    mode_cmd_no_target("-i", "unlock", channel or "", event)
