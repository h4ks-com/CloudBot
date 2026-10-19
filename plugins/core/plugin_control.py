from operator import itemgetter
from pathlib import Path
from typing import Annotated

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import web
from cloudbot.util.formatting import gen_markdown_table


@hook.command(permissions=["botcontrol"], autohelp=False)
def pluginlist(bot: CloudBot):
    """- List all currently loaded plugins"""
    manager = bot.plugin_manager
    plugins = [
        (
            plugin.title,
            str(Path(plugin.file_path).resolve().relative_to(bot.base_dir)),
        )
        for plugin in manager.plugins.values()
    ]
    plugins.sort(key=itemgetter(0))
    table = gen_markdown_table(["Plugin", "Path"], plugins)
    return web.paste(table, service="hastebin")


@hook.command(permissions=["botcontrol"])
async def pluginload(
    bot: CloudBot,
    event: CommandEvent,
    path: Annotated[str, Arg("Path of the plugin file")],
):
    """(Re)load <path> manually"""
    manager = bot.plugin_manager
    path = str(Path(path.strip()).resolve())
    was_loaded = path in manager.plugins
    coro = bot.plugin_manager.load_plugin(path)

    try:
        await coro
    except Exception:
        event.reply("Plugin failed to load.")
        raise
    else:
        return "Plugin {}loaded successfully.".format(
            "re" if was_loaded else ""
        )


@hook.command(permissions=["botcontrol"])
async def pluginunload(
    bot: CloudBot, path: Annotated[str, Arg("Path of the plugin file")]
):
    """Unload <path> manually"""
    manager = bot.plugin_manager
    path = str(Path(path.strip()).resolve())
    is_loaded = path in manager.plugins

    if not is_loaded:
        return "Plugin not loaded, unable to unload."

    if await manager.unload_plugin(path):
        return "Plugin unloaded successfully."

    return "Plugin failed to unload."
