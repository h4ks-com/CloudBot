import re
from typing import Annotated

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg, OptionalChannel
from cloudbot.util import formatting


@hook.command(
    "groups",
    "listgroups",
    "permgroups",
    permissions=["permissions_users"],
    autohelp=False,
)
async def get_permission_groups(conn: IrcClient) -> str:
    """- lists all valid groups"""
    return f"Valid groups: {conn.permissions.get_groups()}"


@hook.command("gperms", permissions=["permissions_users"])
async def get_group_permissions(
    conn: IrcClient,
    event: CommandEvent,
    group: Annotated[str, Arg("Group to list", rest=False)],
) -> str | None:
    """lists permissions given to <group>"""
    permission_manager = conn.permissions
    if not permission_manager.group_exists(group):
        event.notice(f"Unknown group '{group}'")
        return None

    group_permissions = permission_manager.get_group_permissions(group.lower())
    if group_permissions:
        return f"Group {group} has permissions {group_permissions}"

    return f"Group {group} exists, but has no permissions"


@hook.command("gusers", permissions=["permissions_users"])
async def get_group_users(
    conn: IrcClient,
    event: CommandEvent,
    group: Annotated[str, Arg("Group to list", rest=False)],
) -> str | None:
    """lists users in <group>"""
    permission_manager = conn.permissions
    if not permission_manager.group_exists(group):
        event.notice(f"Unknown group '{group}'")
        return None

    group_users = permission_manager.get_group_users(group.lower())
    if group_users:
        return f"Group {group} has members: {group_users}"

    return f"Group {group} exists, but has no members"


def parse_self(event: CommandEvent, user: str | None) -> str | None:
    if user:
        if not event.has_permission("permissions_users"):
            event.notice(
                "Sorry, you are not allowed to use this command on another user"
            )
            return None

        return user

    return event.mask


@hook.command("uperms", autohelp=False)
async def get_user_permissions(
    event: CommandEvent,
    user: Annotated[
        str | None, Arg("User to look up, yourself by default", rest=False)
    ] = None,
) -> str | None:
    """lists all permissions given to a user, yourself by default"""
    user = parse_self(event, user)
    if not user:
        return None

    permission_manager = event.conn.permissions

    user_permissions = permission_manager.get_user_permissions(user.lower())
    if user_permissions:
        return f"User {user} has permissions: {user_permissions}"

    return f"User {user} has no elevated permissions"


@hook.command("ugroups", autohelp=False)
async def get_user_groups(
    event: CommandEvent,
    user: Annotated[
        str | None, Arg("User to look up, yourself by default", rest=False)
    ] = None,
) -> str | None:
    """lists the permission groups of a user, yourself by default"""
    user = parse_self(event, user)
    if not user:
        return None

    permission_manager = event.conn.permissions

    user_groups = permission_manager.get_user_groups(user.lower())
    if user_groups:
        return f"User {user} is in groups: {user_groups}"

    return f"User {user} is in no permission groups"


def remove_user_from_group(user: str, group: str, event: CommandEvent) -> bool:
    permission_manager = event.conn.permissions
    changed_masks = permission_manager.remove_group_user(
        group.lower(), user.lower()
    )

    mask_list = formatting.get_text_list(changed_masks, "and")
    event.reply(f"Removed {mask_list} from {group}")
    event.admin_log(
        f"{event.nick} used deluser remove {mask_list} from {group}."
    )

    return bool(changed_masks)


@hook.command("deluser", permissions=["permissions_users"])
async def remove_permission_user(
    event: CommandEvent,
    bot: CloudBot,
    conn: IrcClient,
    user: Annotated[str, Arg("User to remove")],
    group: Annotated[
        str | None, Arg("Group to remove the user from", rest=False)
    ] = None,
):
    """removes <user> from [group], or from all groups if no group is specified"""
    perm_manager = conn.permissions
    if group:
        if not perm_manager.group_exists(group.lower()):
            event.notice(f"Unknown group '{group}'")
            return

        groups = [group] if perm_manager.user_in_group(user, group) else []
    else:
        groups = perm_manager.get_user_groups(user.lower())

    if not groups:
        event.reply(f"No masks with elevated permissions matched {user}")
        return

    changed = False
    for user_group in groups:
        if remove_user_from_group(user, user_group, event):
            changed = True

    if changed:
        bot.config.save_config()
        perm_manager.reload()


@hook.command("adduser", permissions=["permissions_users"])
async def add_permissions_user(
    nick: str,
    conn: IrcClient,
    bot: CloudBot,
    event: CommandEvent,
    user: Annotated[str, Arg("User mask in the format nick!user@host")],
    group: Annotated[str, Arg("Group to add the user to", rest=False)],
):
    """adds <user> to <group>"""
    if not re.search(".+!.+@.+", user):
        # TODO: When we have presence tracking, check if there are any users in the channel with the nick given
        event.notice("The user must be in the format 'nick!user@host'")
        return

    permission_manager = conn.permissions

    group_exists = permission_manager.group_exists(group)

    if not permission_manager.add_user_to_group(user.lower(), group.lower()):
        event.reply(f"User {user} is already matched in group {group}")
        return None

    if group_exists:
        event.reply(f"User {user} added to group {group}")
        event.admin_log(f"{nick} used adduser to add {user} to {group}.")
    else:
        event.reply(f"Group {group} created with user {user}")
        event.admin_log(
            f"{nick} used adduser to create group {group} and add {user} to it."
        )

    bot.config.save_config()
    permission_manager.reload()


@hook.command("stopthebot", permissions=["botcontrol"])
async def stop(
    bot: CloudBot, reason: Annotated[str | None, Arg("Quit message")] = None
):
    """stops me with [reason] as its quit message."""
    if reason:
        await bot.stop(reason=reason)
    else:
        await bot.stop()


@hook.command(permissions=["botcontrol"])
async def restart(
    bot: CloudBot, reason: Annotated[str | None, Arg("Quit message")] = None
):
    """restarts me with [reason] as its quit message."""
    if reason:
        await bot.restart(reason=reason)
    else:
        await bot.restart()


@hook.command("rehash", "reload", permissions=["botcontrol"])
async def rehash_config(bot: CloudBot) -> str:
    """- Rehash config"""
    await bot.reload_config()
    return "Config reloaded."


@hook.command(permissions=["botcontrol", "snoonetstaff"])
async def join(
    conn: IrcClient,
    nick: str,
    event: CommandEvent,
    channel: Annotated[str, Arg("Channel to join", kind="channel")],
    key: Annotated[str | None, Arg("Channel key", rest=False)] = None,
):
    """joins <channel> with the optional [key]"""
    if not channel.startswith("#"):
        channel = f"#{channel}"

    event.admin_log(f"{nick} used JOIN to make me join {channel}.")
    event.notice(f"Attempting to join {channel}...")
    conn.join(channel, key)


def parse_targets(text: str | None, chan: str) -> list[str]:
    if text:
        targets = text
    else:
        targets = chan

    return targets.split()


@hook.command(permissions=["botcontrol", "snoonetstaff"], autohelp=False)
async def part(
    conn: IrcClient,
    nick: str,
    chan: str,
    event: CommandEvent,
    channel: Annotated[
        str | None,
        Arg(
            "Channels to leave, the current one by default",
            kind="channel",
            rest=True,
        ),
    ] = None,
):
    """parts [channel], or the caller's channel if no channel is specified"""
    for target in parse_targets(channel, chan):
        event.admin_log(f"{nick} used PART to make me leave {target}.")
        event.notice(f"Attempting to leave {target}...")
        conn.part(target)


@hook.command(autohelp=False, permissions=["botcontrol"])
async def cycle(
    conn: IrcClient,
    chan: str,
    event: CommandEvent,
    channel: Annotated[
        str | None,
        Arg(
            "Channels to cycle, the current one by default",
            kind="channel",
            rest=True,
        ),
    ] = None,
):
    """cycles [channel], or the caller's channel if no channel is specified"""
    for target in parse_targets(channel, chan):
        event.notice(f"Attempting to cycle {target}...")
        conn.part(target)
        conn.join(target)


@hook.command("nick", permissions=["botcontrol"])
async def change_nick(
    conn: IrcClient,
    event: CommandEvent,
    new_nick: Annotated[str, Arg("New nickname", rest=False)],
) -> None:
    """changes my nickname to <nick>"""
    if not event.is_nick_valid(new_nick):
        event.notice(f"Invalid username '{new_nick}'")
        return

    event.notice(f"Attempting to change nick to '{new_nick}'...")
    conn.target_nick = new_nick
    conn.set_nick(new_nick)


@hook.command(permissions=["botcontrol"])
async def raw(
    conn: IrcClient,
    event: CommandEvent,
    command: Annotated[str, Arg("Raw IRC line to send")],
) -> None:
    """sends <command> as a raw IRC command"""
    event.notice("Raw command sent.")
    conn.send(command)


@hook.command(permissions=["botcontrol", "snoonetstaff"])
async def say(
    conn: IrcClient,
    chan: str,
    nick: str,
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    message: Annotated[str, Arg("Text to say")],
):
    """says <message> to [channel], or to the caller's channel if no channel is specified"""
    channel = channel or chan
    event.admin_log(f'{nick} used SAY to make me SAY "{message}" in {channel}.')
    conn.message(channel, message)


@hook.command("message", "sayto", permissions=["botcontrol", "snoonetstaff"])
async def send_message(
    conn: IrcClient,
    nick: str,
    event: CommandEvent,
    name: Annotated[str, Arg("Nick or channel to message")],
    message: Annotated[str, Arg("Text to say")],
):
    """says <message> to <name>"""
    event.admin_log(
        f'{nick} used MESSAGE to make me SAY "{message}" in {name}.'
    )
    conn.message(name, message)


@hook.command("me", "act", permissions=["botcontrol", "snoonetstaff"])
async def me(
    conn: IrcClient,
    chan: str,
    nick: str,
    event: CommandEvent,
    *,
    channel: OptionalChannel = None,
    action: Annotated[str, Arg("Action to act out")],
) -> None:
    """acts out <action> in a [channel], or in the current channel if none is specified"""
    channel = channel or chan
    event.admin_log(f'{nick} used ME to make me ACT "{action}" in {channel}.')
    conn.ctcp(channel, "ACTION", action)


@hook.command(autohelp=False, permissions=["botcontrol"])
async def listchans(conn: IrcClient, chan: str, event: CommandEvent) -> None:
    """- Lists the current channels the bot is in"""
    chans = ", ".join(sorted(conn.channels, key=lambda x: x.strip("#").lower()))
    lines = formatting.chunk_str(f"I am currently in: {chans}")
    func = event.notice if chan[:1] == "#" else event.message
    for line in lines:
        func(line)
