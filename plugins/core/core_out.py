"""
Core filters for IRC raw lines
"""

from cloudbot import hook
from cloudbot.hook import Priority
from cloudbot.util import colors

NEW_LINE_TRANS_TBL = str.maketrans(
    {
        "\r": None,
        "\n": None,
        "\0": None,
    }
)


@hook.irc_out(priority=Priority.HIGHEST)
def strip_newlines(line, conn):
    """
    Removes newline characters from a message
    :param line: str
    :param conn: cloudbot.clients.irc.IrcClient
    :return: str
    """
    do_strip = conn.config.get("strip_newlines", True)
    if do_strip:
        return line.translate(NEW_LINE_TRANS_TBL)

    return line


@hook.irc_out(priority=Priority.HIGH)
def truncate_line(line, conn):
    line_len = conn.config.get("max_line_length", 510)
    # The IRCv3 message-tags section has its own (larger) budget and is not part
    # of the 512-byte message limit, so the two are truncated separately. Limits
    # are byte budgets, so slice on the UTF-8 encoding to avoid overflowing them
    # (and decode back lossily rather than emit a split codepoint).
    if line.startswith("@"):
        tag_limit = conn.config.get("max_tags_length", 8191)
        tags, sep, rest = line.partition(" ")
        if sep:
            tags = tags.encode("utf-8")[:tag_limit].decode("utf-8", "ignore")
            rest = rest.encode("utf-8")[:line_len].decode("utf-8", "ignore")
            return tags + " " + rest + "\r\n"
    return line[:line_len] + "\r\n"


@hook.irc_out(priority=Priority.LOWEST)
def encode_line(line, conn):
    if not isinstance(line, str):
        return line

    encoding = conn.config.get("encoding", "utf-8")
    errors = conn.config.get("encoding_errors", "replace")
    return line.encode(encoding, errors)


@hook.irc_out(priority=Priority.HIGH)
def strip_command_chars(parsed_line, conn, line):
    chars = conn.config.get("strip_cmd_chars", "!.@;$")
    if (
        chars
        and parsed_line
        and parsed_line.command == "PRIVMSG"
        and parsed_line.parameters[-1][0] in chars
    ):
        new_msg = (
            colors.parse("$(red)[!!]$(clear) ") + parsed_line.parameters[-1]
        )
        parsed_line.parameters[-1] = new_msg
        parsed_line.has_trail = True
        return parsed_line

    return line
