import re
from typing import Annotated

from sqlalchemy import Column, Integer, String, Table
from sqlalchemy.orm import Session

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
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
aliases_cache: dict[str, dict[str, str]] = {}


@hook.on_start()
def load_cache(db) -> None:
    """
    Load aliases from the database into the cache
    """
    aliases_cache.clear()

    for row in db.execute(aliases_table.select()):
        nick = row["nick"].lower()
        if nick not in aliases_cache:
            aliases_cache[nick] = {}

        aliases_cache[nick][row["name"].lower()] = row["cmdline"]


@hook.command("aliasadd", autohelp=False)
def add_alias(text: str, nick: str, db: Session, event: CommandEvent) -> None:
    """
    <name> = <cmdline> - Adds a new alias with the given name and commands
    """
    if not text:
        event.reply("Usage: .addalias <name> = <cmdline>")
        return

    match = re.match(r"(\S+)\s*=\s+(.*)", text)
    if not match:
        event.reply("Usage: .addalias <name> = <cmdline>")
        return

    name, cmdline = match.groups()
    name = name.lower()

    nick_lower = nick.lower()
    if nick_lower not in aliases_cache:
        aliases_cache[nick_lower] = {}

    # Check if alias already exists
    res = db.execute(
        aliases_table.select()
        .where(aliases_table.c.nick == nick_lower)
        .where(aliases_table.c.name == name)
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
    event.reply(f"Alias '{name}' added successfully.")


@hook.command("aliasdel", "aliasrm", "aliasremove")
def delete_alias(
    nick: str,
    db: Session,
    event: CommandEvent,
    name: Annotated[str, Arg("Name of the alias to delete")],
) -> None:
    """
    Deletes the alias with the given name
    """
    name = name.lower()
    nick_lower = nick.lower()

    if nick_lower not in aliases_cache or name not in aliases_cache[nick_lower]:
        event.reply(
            f"You do not have an alias named '{name}' or you are trying to delete an alias for another user?"
        )
        return

    db.execute(
        aliases_table.delete()
        .where(aliases_table.c.nick == nick_lower)
        .where(aliases_table.c.name == name)
    )

    db.commit()
    del aliases_cache[nick_lower][name]
    if not aliases_cache[nick_lower]:
        del aliases_cache[nick_lower]

    event.reply(f"Alias '{name}' deleted successfully.")


@hook.command("aliases", "aliaslist", autohelp=False)
def list_aliases(
    nick: str,
    event: CommandEvent,
    user: Annotated[
        str | None, Arg("Nick whose aliases to list", kind="user")
    ] = None,
) -> None:
    """
    Lists all aliases for the user or yourself
    """
    nick_lower = (user or nick).lower()
    if nick_lower not in aliases_cache or not aliases_cache[nick_lower]:
        event.reply(f"No aliases found for '{nick_lower}'.")
        return

    if nick_lower != nick.lower():
        event.notice(f"Aliases for {nick_lower}:")
    else:
        event.notice("Your aliases:")
    for name, cmdline in aliases_cache[nick_lower].items():
        event.notice(f"{name}: {cmdline}")


@hook.command("aliascopy", "aliasimport")
def copy_alias(
    nick: str,
    db: Session,
    event: CommandEvent,
    source_nick: Annotated[
        str, Arg("Nick to copy the alias from", kind="user")
    ],
    alias_name: Annotated[str, Arg("Name of the alias to copy")],
) -> None:
    """
    Copies an alias from another user
    """
    source_nick_lower = source_nick.lower()
    alias_name_lower = alias_name.lower()

    if (
        source_nick_lower not in aliases_cache
        or alias_name_lower not in aliases_cache[source_nick_lower]
    ):
        event.reply(f"Alias '{alias_name}' not found for user '{source_nick}'.")
        return

    cmdline = aliases_cache[source_nick_lower][alias_name_lower]

    # Add the alias to the current user's aliases
    db.execute(
        aliases_table.insert().values(
            nick=nick.lower(), name=alias_name_lower, cmdline=cmdline
        )
    )
    db.commit()

    aliases_cache.setdefault(nick.lower(), {})[alias_name_lower] = cmdline
    event.reply(
        f"Alias '{alias_name}' copied successfully from '{source_nick}'."
    )


@hook.command("alias", "a")
async def run_alias(
    nick: str,
    bot: CloudBot,
    event: CommandEvent,
    name: Annotated[str, Arg("Name of the alias to run")],
    cmdargs: Annotated[str | None, Arg("Arguments for the alias")] = None,
) -> str:
    """
    Executes the alias with the given name optionally with arguments.

    Arguments are appended to the command if no placeholder "<>" is used in the alias definition.
    """
    cmdargs = cmdargs or ""
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
