import re
from itertools import cycle

from cloudbot import hook
from cloudbot.util import formatting
from plugins.mock import get_latest_line

correction_re = re.compile(
    r"^(?:[sS]/(?:((?:\\/|[^/])*?)(?<!\\)/((?:\\/|[^/])*?)(?:(?<!\\)/([igx]{,4}))?)\s*?;*?)(?:;\s*?[sS]/(?:((?:\\/|[^/])*?)(?<!\\)/((?:\\/|[^/])*?)(?:(?<!\\)/([igx]{,4}))?)\s*?;*?)*?$"
)
exp_re = re.compile(
    r"(?:[sS]/(?:((?:\\/|[^/])*)(?<!\\)/((?:\\/|[^/])*)(?:(?<!\\)/([igx]{,4}))?))"
)
unescape_re = re.compile(r"\\(.)")

LAMESIZE = 15

REFLAGS = {
    "i": re.IGNORECASE,
    "g": re.MULTILINE,
    "x": re.VERBOSE,
}

# Config-based replacement commands
REPLACEMENT_COMMANDS = {
    "valware": {
        "pattern": r"\s+",
        "replacements": [
            " \x02but also unrealircd and\x02 ",
            " \x02and some n8n and\x02 ",
        ],
        "flags": re.MULTILINE | re.IGNORECASE,
    },
    "mattf": {
        "pattern": r"\s+",
        "replacements": [
            " \x02and docker build also\x02 ",
            " \x02but containers though\x02 ",
            " \x02with some kubernetes and\x02 ",
        ],
        "flags": re.MULTILINE | re.IGNORECASE,
    },
    "handyc": {
        "pattern": r"\s+",
        "replacements": [
            "\x02, but in China and\x02 ",
            " \x02and Buddhism and\x02 ",
        ],
        "flags": re.MULTILINE | re.IGNORECASE,
    },
    "bub": {
        "pattern": r"\s+",
        "replacements": [
            "\x02, but GI Joe is so good\x02 ",
            " \x02and Minecraft and\x02 ",
            " \x02and Dzogchen and\x02 ",
        ],
        "flags": re.MULTILINE | re.IGNORECASE,
    },
    "doesnm": {
        "pattern": r"\s+",
        "replacements": [
            " \x02ollama is just a wrapper over llama.cpp\x02 ",
            "\x02, but in Podolsk and\x02 ",
            " \x02and Delta Chat and\x02 ",
            " \x02but also containers though\x02 ",
        ],
        "flags": re.MULTILINE | re.IGNORECASE,
    },
}


def get_flags(flags, message):
    re_flags = []
    for flag in flags:
        if flag not in "igx":
            message(
                "Invalid regex flag `{}`. Valid are: [{}]".format(
                    flag, ", ".join(REFLAGS.keys())
                )
            )
        re_flags.append(REFLAGS[flag])
    return re_flags


def paser_sed_exp(groups, message):
    find = groups[0]
    replace = groups[1] if groups[1] else ""
    flags = str(groups[2]) if groups[2] else ""
    return find, replace, get_flags(flags, message)


def create_replacement_command(command_name: str):
    """Factory function to create replacement commands from config."""
    config = REPLACEMENT_COMMANDS[command_name]

    def replacement_func(
        bot, reply, text: str, chan: str, nick: str, conn, stdin=None
    ) -> str:
        if stdin:
            line = stdin
        else:
            if not text:
                return f"Usage: {command_name} <nick>"

            target_nick = text.split()[0]
            line = get_latest_line(None, conn, chan, target_nick)

            if line is None:
                return f"Nothing found in recent history for {target_nick}"

        replacements = config["replacements"]
        replacement_cycle = cycle(replacements)

        def cyclic_replace(match):
            """Replace function that cycles through replacements for each match."""
            return next(replacement_cycle)

        # Apply the replacement with cycling
        new = re.sub(
            config["pattern"],
            cyclic_replace,
            line,
            flags=config["flags"],
        )

        return formatting.truncate(f"\x02{new}", 420)

    # Set the docstring
    replacement_func.__doc__ = f"<nick> - make the last sentence from <nick> as if {command_name} had said it"
    return replacement_func


@hook.regex(correction_re)
def correction(match, conn, nick, chan, message):
    # groups = [unescape_re.sub(r"\1", group or "") for group in match.groups()]
    find, replace, re_flags = paser_sed_exp(match.groups(), message)

    max_i = 50000
    i = 0

    for name, _timestamp, msg in reversed(conn.history[chan]):
        if i >= max_i:
            break
        i += 1
        if correction_re.match(msg):
            # don't correct corrections, it gets really confusing
            continue

        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {} {}"
        else:
            mod_msg = msg
            fmt = "<{}> {}"

        new = re.sub(
            find,
            "\x02" + replace + "\x02",
            mod_msg,
            count=re.MULTILINE not in re_flags,
            flags=sum(re_flags),
        )
        if new != mod_msg:
            find_esc = re.escape(find)
            replace_esc = re.escape(new)
            mod_msg = unescape_re.sub(r"\1", new)
            for exp in re.findall(exp_re, match[0])[1:]:
                if not exp:
                    continue
                find, replace, flags = exp
                re_flags = get_flags(flags, message)
                new = re.sub(
                    find,
                    "\x02" + replace + "\x02",
                    mod_msg,
                    count=re.MULTILINE not in re_flags,
                    flags=sum(re_flags),
                )
                find_esc = re.escape(find)
                replace_esc = re.escape(new)
                mod_msg = unescape_re.sub(r"\1", new)

            # mod_msg = ireplace(re.escape(mod_msg), find_esc, "\x02" + replace_esc + "\x02")
            mod_msg = formatting.truncate(unescape_re.sub(r"\1", mod_msg), 420)
            message(f"Correction, {fmt.format(name, mod_msg)}")
            break

    else:
        return "No match"


@hook.command("sed", autohelp=False)
def sed(bot, reply, text: str) -> str:
    """s/<part1>/<part2>/<flags> <args> - Perform regex substitution on the given text."""
    if not text:
        return "Usage: sed s/<part1>/<part2>/<flags> <args>"

    match = exp_re.match(text)
    if not match:
        return "Invalid format. Use: s/<part1>/<part2>/<flags> <args>"

    find, replace, re_flags = paser_sed_exp(match.groups(), reply)
    args = text[match.end() :].strip()

    if not args:
        return "No text provided to process. Did you forget an ending '/'?"

    new = re.sub(
        find,
        "\x02" + replace + "\x02",
        args,
        count=re.MULTILINE not in re_flags,
        flags=sum(re_flags),
    )
    return formatting.truncate(new, 420)


# Dynamically create and register replacement commands
for cmd_name in REPLACEMENT_COMMANDS.keys():
    # Create the function
    func = create_replacement_command(cmd_name)

    # Register it as a command
    hook_decorator = hook.command(cmd_name, autohelp=False)
    registered_func = hook_decorator(func)

    # Add to global namespace so CloudBot can find it
    globals()[cmd_name] = registered_func
