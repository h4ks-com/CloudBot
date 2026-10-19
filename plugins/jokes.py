import random
from pathlib import Path
from typing import Annotated

from cloudbot import hook
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg

joke_lines = {}


def load_joke_file(path: Path) -> list[str]:
    """Loads all the lines from a file, excluding blanks and lines that have been 'commented out'."""
    with path.open(encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.startswith("//")
        ]


@hook.on_start()
def load_jokes(bot):
    """Load strings into memory from files in the data directory.
    Put 'NAME.txt' in `file_list` to make those strings available as `joke_lines['NAME']`.
    """
    file_list = [
        "yo_momma.txt",
        "do_it.txt",
        "puns.txt",
        "confucious.txt",
        "one_liners.txt",
        "wisdom.txt",
        "book_puns.txt",
        "lawyerjoke.txt",
        "kero.txt",
    ]
    for file_name in file_list:
        file_path = bot.data_path / file_name
        joke_lines[file_path.stem] = load_joke_file(file_path)


@hook.command(
    "yomomma",
    "yourmother",
    "yomumma",
    "yomama",
    "yomommy",
    "urmum",
    autohelp=False,
)
def yomomma(
    nick: str,
    conn: IrcClient,
    event: CommandEvent,
    target: Annotated[
        str | None, Arg("Who gets the joke, you by default", kind="user")
    ] = None,
):
    """Tells a yo momma joke to the nick."""
    target = (target or "").strip() or nick
    if not event.is_nick_valid(target) or target.lower() == conn.nick.lower():
        target = nick
    joke = random.choice(joke_lines["yo_momma"]).lower()
    return f"{target}, {joke}"


@hook.command(autohelp=False)
def doit(event: CommandEvent):
    """- Prints a do it line, example: mathematicians do with a pencil."""
    event.message(random.choice(joke_lines["do_it"]))


@hook.command(autohelp=False)
def pun(event: CommandEvent):
    """- Come on everyone loves puns right?"""
    event.message(random.choice(joke_lines["puns"]))


@hook.command(autohelp=False)
def confucious(event: CommandEvent):
    """- Confucious say man standing on toilet is high on pot.
    (Note that the spelling is deliberate: https://www.urbandictionary.com/define.php?term=Confucious)
    """
    saying = random.choice(joke_lines["confucious"]).lower()
    event.message(f"Confucious say {saying}")


@hook.command(autohelp=False)
def dadjoke(event: CommandEvent):
    """- Love em or hate em, bring on the dad jokes."""
    event.message(random.choice(joke_lines["one_liners"]))


@hook.command(autohelp=False)
def wisdom(event: CommandEvent):
    """- Words of wisdom from various bathroom stalls."""
    event.message(random.choice(joke_lines["wisdom"]))


@hook.command(autohelp=False)
def bookpun(event: CommandEvent):
    """- Suggests a pun of a book title/author."""
    # suggestions = ["Why not try", "You should read", "You gotta check out"]
    event.message(random.choice(joke_lines["book_puns"]))


@hook.command("boobs", "boobies")
def boobies(phrase: Annotated[str, Arg("Text to improve")]):
    """Everything is better with boobies!"""
    boob = "\u2299"
    out = phrase.strip()
    out = out.replace("o", boob).replace("O", boob).replace("0", boob)
    if out == phrase.strip():
        return f"Sorry I couldn't turn anything in '{out}' into boobs for you."
    return out


@hook.command(autohelp=False)
def zombs():
    """- Prints some fucked up shit."""
    out = "\u2299\u2299\u0505\u0f0d\u0020\u0e88\u0020\u25de\u0c6a\u25df\u0e88\u0020\u0f0d\u0648"
    return out


@hook.command("awesome", "iscool", "cool")
def awesome(
    event: CommandEvent,
    target: Annotated[str, Arg("Who is awesome", kind="user")],
):
    """Returns a link to show the nick how awesome they are.
    See https://github.com/sebastianbarfurth/is-awesome.cool
    """
    target = target.split(" ")[0]
    if not event.is_nick_valid(target):
        return f"Sorry I can't tell {target} how awesome they are."
    link = f"http://{target}.is-awesome.cool/"
    return f"{target}: I am blown away by your recent awesome action(s). Please read \x02{link}\x02"


@hook.command(autohelp=False)
def triforce(event: CommandEvent):
    """- Returns a triforce!"""
    top = ["\u00a0\u25b2", "\u00a0\u00a0\u25b2", "\u25b2", "\u00a0\u25b2"]
    bottom = ["\u25b2\u00a0\u25b2", "\u25b2 \u25b2", "\u25b2\u25b2"]
    event.message(random.choice(top))
    event.message(random.choice(bottom))


@hook.command("kero", "kerowhack")
def kero(phrase: Annotated[str, Arg("Text to say")]):
    """Returns the text input the way kerouac5 would say it."""
    keror = random.choice(joke_lines["kero"]).upper()
    if keror == "???? WTF IS":
        out = keror + " " + phrase.upper()
    else:
        out = phrase.upper() + " " + keror
    return out


@hook.command(autohelp=False)
def lawyerjoke(event: CommandEvent):
    """- Returns a lawyer joke, so lawyers know how much we hate them."""
    event.message(random.choice(joke_lines["lawyerjoke"]))


@hook.command(autohelp=False)
def fuck():
    """- Returns something funny."""
    return "something funny."
