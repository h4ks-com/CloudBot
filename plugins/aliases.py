import re

from sqlalchemy import Column, Integer, String, Table

from cloudbot import hook
from cloudbot.util import database

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
def load_cache(db):
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


@hook.command("addalias", autohelp=False)
def add_alias(text, nick, db, reply, notice):
    """
    .addalias <name> <cmdline> - Adds a new alias with the given name and commands
    """
    if not text:
        reply("Usage: .addalias <name> <cmdline>")
        return

    match = re.match(r"(\S+)\s+(.*)", text)
    if not match:
        reply("Usage: .addalias <name> <cmdline>")
        return

    name, cmdline = match.groups()
    name = name.lower()

    if nick not in aliases_cache:
        aliases_cache[nick] = {}

    # Check if alias already exists
    res = db.execute(
        aliases_table.select().where(aliases_table.c.nick == nick.lower()).where(aliases_table.c.name == name)
    ).fetchone()

    if res:
        db.execute(
            aliases_table.update()
            .where(aliases_table.c.nick == nick.lower())
            .where(aliases_table.c.name == name)
            .values(cmdline=cmdline)
        )
    else:
        db.execute(aliases_table.insert().values(nick=nick.lower(), name=name, cmdline=cmdline))

    db.commit()
    aliases_cache[nick.lower()][name] = cmdline
    reply(f"Alias '{name}' added successfully.")


@hook.command("delalias", autohelp=False)
def delete_alias(text, nick, db, reply, notice):
    """
    .delalias <name> - Deletes the alias with the given name
    """
    if not text:
        reply("Usage: .delalias <name>")
        return

    name = text.strip().lower()
    nick_lower = nick.lower()

    if nick_lower not in aliases_cache or name not in aliases_cache[nick_lower]:
        reply(f"Alias '{name}' not found.")
        return

    db.execute(aliases_table.delete().where(aliases_table.c.nick == nick_lower).where(aliases_table.c.name == name))

    db.commit()
    del aliases_cache[nick_lower][name]
    if not aliases_cache[nick_lower]:
        del aliases_cache[nick_lower]

    reply(f"Alias '{name}' deleted successfully.")


@hook.command("aliases", autohelp=False)
def list_aliases(nick, reply, notice):
    """
    .aliases - Lists all aliases for the user
    """
    nick_lower = nick.lower()
    if nick_lower not in aliases_cache or not aliases_cache[nick_lower]:
        reply("You have no aliases.")
        return

    notice("Your aliases:")
    for name, cmdline in aliases_cache[nick_lower].items():
        notice(f"{name}: {cmdline}")


@hook.command("alias", "a", autohelp=False)
def run_alias(text, nick, bot, event):
    """
    .alias <name> - Executes the alias with the given name
    """
    if not text:
        return "Usage: .alias <name>"

    name = text.strip().split()[0].lower()
    nick_lower = nick.lower()

    if nick_lower not in aliases_cache or name not in aliases_cache[nick_lower]:
        return f"Alias '{name}' not found."

    cmdline = aliases_cache[nick_lower][name]

    # Create a new event to process the command
    cmd_event = event.copy()
    cmd_event.text = cmdline
    cmd_event.cmd_prefix = "."  # Ensure the command has the right prefix

    # Process the command through the bot's command dispatcher
    return bot.process(cmd_event)
