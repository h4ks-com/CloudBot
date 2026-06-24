from cloudbot import hook
from cloudbot.util import formatting, web


def get_potential_commands(bot, cmd_name):
    cmd_name = cmd_name.lower().strip()
    try:
        yield cmd_name, bot.plugin_manager.commands[cmd_name]
    except LookupError:
        for name, _hook in bot.plugin_manager.commands.items():
            if name.startswith(cmd_name):
                yield name, _hook


def help_flood(bot, chan, notice, message, has_permission, triggered_prefix):
    commands = []
    for command in bot.plugin_manager.command_infos():
        if command.permissions and not any(
            has_permission(perm, notice=False) for perm in command.permissions
        ):
            continue
        commands.append(command.name)

    # list of lines to send to the user
    lines = formatting.chunk_str(
        "Here's a list of commands you can use: " + ", ".join(commands)
    )

    for line in lines:
        if chan[:1] == "#":
            notice(line)
        else:
            # This is an user in this case.
            message(line)

    notice(
        "For detailed help, use {}help <command>, without the brackets.".format(
            triggered_prefix
        )
    )


@hook.command("help", autohelp=False)
async def help_command(
    text, chan, bot, notice, message, has_permission, triggered_prefix
):
    """[command] - gives help for [command], or lists all available commands if no command is specified"""
    if text:
        searching_for = text.lower().strip()
    else:
        searching_for = None

    if text:
        cmds = list(get_potential_commands(bot, text))
        if not cmds:
            notice(f"Unknown command '{text}'")
            return

        if len(cmds) > 1:
            notice(
                "Possible matches: {}".format(
                    formatting.get_text_list(
                        sorted([command for command, _ in cmds])
                    )
                )
            )
            return

        doc = cmds[0][1].doc

        if doc:
            notice(f"{triggered_prefix}{searching_for} {doc}")
        else:
            notice(
                "Command {} has no additional documentation.".format(
                    searching_for
                )
            )
    else:
        webhooks_config = bot.config.get("webhooks", {})
        base_url = webhooks_config.get("base_url")
        if base_url:
            return base_url
        help_flood(bot, chan, notice, message, has_permission, triggered_prefix)


@hook.command()
async def cmdinfo(text, bot, notice):
    """<command> - Gets various information about a command"""
    name = text.split()[0]
    cmds = list(get_potential_commands(bot, name))
    if not cmds:
        notice(f"Unknown command: '{name}'")
        return

    if len(cmds) > 1:
        notice(
            "Possible matches: {}".format(
                formatting.get_text_list(
                    sorted([command for command, plugin in cmds])
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

    notice(info)


@hook.command(permissions=["botcontrol"], autohelp=False)
def generatehelp(conn, bot):
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
