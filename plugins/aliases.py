import re

from sqlalchemy import Column, Integer, String, Table

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.util import database
from plugins.chain import get_hook_from_command, wrap_event

aliases_table = Table(
    "aliases",
    database.metadata,
    Column("id", Integer, primary_key=True),
    Column("nick", String),
    Column("name", String),
    Column("cmdline", String),
)

# Store aliases in memory for faster access
aliases_cache = {}


@hook.on_start()
def load_cache(db) -> None:
    """
    Load aliases from the database into the cache
    """
    global aliases_cache
    aliases_cache = {}

    for row in db.execute(aliases_table.select()):
        nick = row["nick"].lower()
        if nick not in aliases_cache:
            aliases_cache[nick] = {}

        aliases_cache[nick][row["name"].lower()] = row["cmdline"]


@hook.command("aliasadd", autohelp=False)
def add_alias(text: str, nick: str, db, reply, notice) -> None:
    """
    .addalias <name> = <cmdline> - Adds a new alias with the given name and commands
    """
    global aliases_cache
    if not text:
        reply("Usage: .addalias <name> = <cmdline>")
        return

    match = re.match(r"(\S+)\s*=\s+(.*)", text)
    if not match:
        reply("Usage: .addalias <name> = <cmdline>")
        return

    name, cmdline = match.groups()
    name = name.lower()

    nick_lower = nick.lower()
    if nick_lower not in aliases_cache:
        aliases_cache[nick_lower] = {}

    # Check if alias already exists
    res = db.execute(
        aliases_table.select().where(aliases_table.c.nick == nick_lower).where(aliases_table.c.name == name)
    ).fetchone()

    if res:
        db.execute(
            aliases_table.update()
            .where(aliases_table.c.nick == nick_lower)
            .where(aliases_table.c.name == name)
            .values(cmdline=cmdline)
        )
    else:
        db.execute(
            aliases_table.insert().values(
                nick=nick_lower,
                name=name,
                cmdline=cmdline,
            )
        )

    db.commit()
    aliases_cache[nick_lower][name] = cmdline
    reply(f"Alias '{name}' added successfully.")


@hook.command("aliasdel", "aliasrm", "aliasremove", autohelp=False)
def delete_alias(text: str, nick: str, db, reply, notice) -> None:
    """
    .delalias <name> - Deletes the alias with the given name
    """
    global aliases_cache
    if not text:
        reply("Usage: .delalias <name>")
        return

    name = text.strip().lower()
    nick_lower = nick.lower()

    if nick_lower not in aliases_cache or name not in aliases_cache[nick_lower]:
        reply(f"You do not have an alias named '{name}' or you are trying to delete an alias for another user?")
        return

    db.execute(aliases_table.delete().where(aliases_table.c.nick == nick_lower).where(aliases_table.c.name == name))

    db.commit()
    del aliases_cache[nick_lower][name]
    if not aliases_cache[nick_lower]:
        del aliases_cache[nick_lower]

    reply(f"Alias '{name}' deleted successfully.")


@hook.command("aliases", "aliaslist", autohelp=False)
def list_aliases(text: str, nick: str, reply, notice) -> None:
    """
    .aliases [nick] - Lists all aliases for the user or yourself
    """
    global aliases_cache
    nick_lower = text.split()[0].lower() if text else nick.lower()
    if nick_lower not in aliases_cache or not aliases_cache[nick_lower]:
        reply(f"No aliases found for '{nick_lower}'.")
        return

    if nick_lower != nick.lower():
        notice(f"Aliases for {nick_lower}:")
    else:
        notice("Your aliases:")
    for name, cmdline in aliases_cache[nick_lower].items():
        notice(f"{name}: {cmdline}")


@hook.command("aliascopy", "aliasimport", autohelp=False)
def copy_alias(text: str, nick: str, db, reply, notice) -> None:
    """
    .aliascopy <source_nick> <alias_name> - Copies an alias from another user
    """
    if not text:
        reply("Usage: .aliascopy <source_nick> <alias_name>")
        return

    parts = text.split()
    if len(parts) != 2:
        reply("Usage: .aliascopy <source_nick> <alias_name>")
        return

    source_nick, alias_name = parts
    source_nick_lower = source_nick.lower()
    alias_name_lower = alias_name.lower()

    if source_nick_lower not in aliases_cache or alias_name_lower not in aliases_cache[source_nick_lower]:
        reply(f"Alias '{alias_name}' not found for user '{source_nick}'.")
        return

    cmdline = aliases_cache[source_nick_lower][alias_name_lower]

    # Add the alias to the current user's aliases
    db.execute(aliases_table.insert().values(nick=nick.lower(), name=alias_name_lower, cmdline=cmdline))
    db.commit()

    aliases_cache.setdefault(nick.lower(), {})[alias_name_lower] = cmdline
    reply(f"Alias '{alias_name}' copied successfully from '{source_nick}'.")


@hook.command("alias", "a", autohelp=False)
async def run_alias(text: str, nick: str, bot: CloudBot, event, reply) -> str:
    """
    .alias <name> [args] - Executes the alias with the given name optionally with arguments.

    Arguments are appended to the command if no placeholder "<>" is used in the alias definition.
    """
    if not text:
        return "Usage: .alias <name> [args]"

    # args may not be present
    name, cmdargs = (text.split(maxsplit=1) + [""])[:2]
    nick_lower = nick.lower()

    if nick_lower not in aliases_cache or name not in aliases_cache[nick_lower]:
        return (
            f"Alias '{name}' not found for you. Use 'aliases' to list your aliases or 'aliascopy' to copy an alias from"
            f" another user."
        )

    cmdline = aliases_cache[nick_lower][name]
    cmdname = cmdline.split()[0]
    if cmdname in ("alias", "a"):
        return "You cannot run an alias from within itself."

    args = cmdline[len(cmdname) :].strip()

    if "<>" in args:
        args = args.replace("<>", cmdargs.strip())
    else:
        args += " " + cmdargs.strip()

    hook = get_hook_from_command(bot, cmdname)

    cmd_event = wrap_event(hook, event, cmdname, args)
    ok, res = await bot.plugin_manager.internal_launch(hook, cmd_event)
    if not ok:
        return "Error occurred while processing the alias."

    # Process the command through the bot's command dispatcher
    return res
