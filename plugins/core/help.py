from typing import Annotated

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting, web


def get_potential_commands(bot, cmd_name):
    cmd_name = cmd_name.lower().strip()
    try:
        yield cmd_name, bot.plugin_manager.commands[cmd_name]
    except LookupError:
        for name, _hook in bot.plugin_manager.commands.items():
            if name.startswith(cmd_name):
                yield name, _hook


def help_flood(bot: CloudBot, chan: str, event: CommandEvent):
    commands = []
    for command in bot.plugin_manager.command_infos():
        if command.permissions and not any(
            event.has_permission(perm, notice=False)
            for perm in command.permissions
        ):
            continue
        commands.append(command.name)

    # list of lines to send to the user
    lines = formatting.chunk_str(
        "Here's a list of commands you can use: " + ", ".join(commands)
    )

    for line in lines:
        if chan[:1] == "#":
            event.notice(line)
        else:
            # This is an user in this case.
            event.message(line)

    event.notice(
        f"For detailed help, use {event.triggered_prefix}help <command>, without the brackets."
    )


@hook.command("help", autohelp=False)
async def help_command(
    chan: str,
    bot: CloudBot,
    event: CommandEvent,
    command: Annotated[str | None, Arg("Command to explain")] = None,
):
    """gives help for the command, or lists all available commands if no command is specified"""
    if command:
        searching_for = command.lower().strip()
    else:
        searching_for = None

    if command:
        cmds = list(get_potential_commands(bot, command))
        if not cmds:
            event.notice(f"Unknown command '{command}'")
            return

        if len({cmd_hook for _, cmd_hook in cmds}) > 1:
            event.notice(
                "Possible matches: {}".format(
                    formatting.get_text_list(
                        sorted([command for command, _ in cmds])
                    )
                )
            )
            return

        doc = cmds[0][1].doc

        if doc:
            event.notice(f"{event.triggered_prefix}{searching_for} {doc}")
        else:
            event.notice(
                f"Command {searching_for} has no additional documentation."
            )
    else:
        webhooks_config = bot.config.get("webhooks", {})
        base_url = webhooks_config.get("base_url")
        if base_url:
            return base_url
        help_flood(bot, chan, event)


@hook.command()
async def cmdinfo(
    bot: CloudBot,
    event: CommandEvent,
    command: Annotated[str, Arg("Command to inspect", rest=False)],
):
    """Gets various information about a command"""
    name = command
    cmds = list(get_potential_commands(bot, name))
    if not cmds:
        event.notice(f"Unknown command: '{name}'")
        return

    if len({cmd_hook for _, cmd_hook in cmds}) > 1:
        event.notice(
            "Possible matches: {}".format(
                formatting.get_text_list(
                    sorted([command for command, _ in cmds])
                )
            )
        )
        return

    cmd_hook = cmds[0][1]

    hook_name = cmd_hook.plugin.title + "." + cmd_hook.function_name
    info = "Command: {}, Aliases: [{}], Hook name: {}".format(
        cmd_hook.name, ", ".join(cmd_hook.aliases), hook_name
    )

    if cmd_hook.permissions:
        info += ", Permissions: [{}]".format(", ".join(cmd_hook.permissions))

    event.notice(info)


@hook.command(permissions=["botcontrol"], autohelp=False)
def generatehelp(conn: IrcClient, bot: CloudBot):
    """- Dumps a list of commands with their help text to the docs directory formatted using markdown."""
    message = f"{conn.nick} Command list\n"
    message += "------\n"
    for command in bot.plugin_manager.command_infos():
        aliases = ", ".join(command.aliases)
        doc = command.description
        if doc:
            doc = (
                doc.replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("[", "&lt;")
                .replace("]", "&gt;")
            )
            if aliases:
                message += f"**{command.name} ({aliases}):** {doc}\n\n"
            else:
                message += f"**{command.name}**: {doc}\n\n"
        else:
            message += f"**{command.name}**: Command has no documentation.\n\n"
        if command.permissions:
            message = message[:-2]
            message += f" ( *Permission required:* {', '.join(command.permissions)})\n\n"
    return web.paste(message)
