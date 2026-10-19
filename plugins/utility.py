"""
utility.py

Provides a number of simple commands for working with strings.

Created By:
    - Luke Rogers <https://github.com/lukeroge>
    - Dabo Ross <https://github.com/daboross>

Special Thanks:
    - Fletcher Boyd <https://github.com/thenoodle68>

License: GPL v3
"""

import base64
import binascii
import codecs
import collections
import hashlib
import random
import re
import shlex
import urllib.parse
from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import colors, formatting, web

COLORS = collections.OrderedDict(
    [
        ("red", "\x0304"),
        ("orange", "\x0307"),
        ("yellow", "\x0308"),
        ("green", "\x0309"),
        ("cyan", "\x0303"),
        ("ltblue", "\x0310"),
        ("rylblue", "\x0312"),
        ("blue", "\x0302"),
        ("magenta", "\x0306"),
        ("pink", "\x0313"),
        ("maroon", "\x0305"),
    ]
)

# helper functions

strip_re = re.compile(r"[\u0003\u0002\u001F\u000F](?:,?\d{1,2}(?:,\d{1,2})?)?")


def strip(string):
    return strip_re.sub("", string)


def translate(text, dic):
    for i, j in dic.items():
        text = text.replace(i, j)
    return text


# misc


@hook.command("qrcode", "qr")
def qrcode(link: Annotated[str, Arg("Link to encode")]):
    """returns a link to a QR code image for the link"""

    args = {
        "cht": "qr",  # chart type (QR)
        "chs": "200x200",  # dimensions
        "chl": link,  # data
    }

    argstring = urllib.parse.urlencode(args)

    link = f"http://chart.googleapis.com/chart?{argstring}"
    return web.try_shorten(link)


# basic text tools


@hook.command("capitalize", "capitalise")
def capitalize(string: Annotated[str, Arg("Text to process")]):
    """Capitalizes the string."""
    return ". ".join([sentence.capitalize() for sentence in string.split(". ")])


@hook.command()
def upper(string: Annotated[str, Arg("Text to process")]):
    """Convert string to uppercase."""
    return string.upper()


@hook.command()
def lower(string: Annotated[str, Arg("Text to process")]):
    """Convert string to lowercase."""
    return string.lower()


@hook.command()
def titlecase(string: Annotated[str, Arg("Text to process")]):
    """Convert string to title case."""
    return string.title()


@hook.command()
def swapcase(string: Annotated[str, Arg("Text to process")]):
    """Swaps the capitalization of the string."""
    return string.swapcase()


@hook.command("aesthetic", "vapor", "fw")
def fullwidth(string: Annotated[str, Arg("Text to process")]):
    """Converts the string to full width characters."""
    HALFWIDTH_TO_FULLWIDTH = str.maketrans(
        '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&()*+,-./:;<=>?@[]^_`{|}~ ',
        "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ！゛＃＄％＆（）＊＋、ー。／：；〈＝〉？＠［］＾＿‘｛｜｝～　",
    )
    return string.translate(HALFWIDTH_TO_FULLWIDTH)


# encoding


@hook.command("rot13")
def rot13_encode(string: Annotated[str, Arg("Text to process")]):
    """Encode the string with rot13."""
    encoder = codecs.getencoder("rot-13")
    return encoder(string)[0]


@hook.command("base64")
def base64_encode(string: Annotated[str, Arg("Text to process")]):
    """Encode the string with base64."""
    return base64.b64encode(string.encode()).decode()


@hook.command("debase64", "unbase64")
def base64_decode(
    event: CommandEvent, string: Annotated[str, Arg("Text to process")]
):
    """Decode the string with base64."""
    try:
        decoded = base64.b64decode(string.encode()).decode(errors="ignore")
    except binascii.Error:
        event.notice(f"Invalid base64 string '{string}'")
        return None

    if repr(decoded)[1:-1] != decoded:
        return (
            "Non printable characters detected in output, "
            f"escaped output: {decoded!r}"
        )

    return decoded


@hook.command("isbase64", "checkbase64")
def base64_check(string: Annotated[str, Arg("Text to process")]):
    """Checks if the string is a valid base64 encoded string"""
    try:
        base64.b64decode(string.encode())
    except binascii.Error:
        return f"'{string}' is not a valid base64 encoded string"
    else:
        return f"'{string}' is a valid base64 encoded string"


@hook.command()
def unescape(string: Annotated[str, Arg("Text to process")]):
    """Unicode unescapes the string."""
    decoder = codecs.getdecoder("unicode_escape")
    return " ".join(decoder(string)[0].splitlines())


@hook.command()
def escape(string: Annotated[str, Arg("Text to process")]):
    """Unicode escapes the string."""
    encoder = codecs.getencoder("unicode_escape")
    return " ".join(encoder(string)[0].decode().splitlines())


# length


@hook.command()
def length(string: Annotated[str, Arg("Text to process")]):
    """Gets the length of the string"""
    return f"The length of that string is {len(string)} characters."


# reverse


@hook.command()
def reverse(string: Annotated[str, Arg("Text to process")]):
    """Reverses the string."""
    return string[::-1]


# hashing


@hook.command("hash")
def hash_command(string: Annotated[str, Arg("Text to process")]):
    """Returns hashes of the string."""
    return ", ".join(
        x + ": " + getattr(hashlib, x)(string.encode("utf-8")).hexdigest()
        for x in ["md5", "sha1", "sha256"]
    )


# novelty


@hook.command()
def munge(text: Annotated[str, Arg("Text to process")]):
    """Munges up the text."""
    return formatting.munge(text)


# Based on plugin by FurCode - <https://github.com/FurCode/RoboCop2>
@hook.command()
def derpify(text: Annotated[str, Arg("Text to process")]):
    """returns some amusing responses from your input."""
    string = text.upper()
    pick_the = random.choice(["TEH", "DA"])
    pick_e = random.choice(["E", "3", "A"])
    pick_qt = random.choice(["?!?!??", "???!!!!??", "?!??!?", "?!?!?!???"])
    pick_ex = random.choice(
        ["1111!11", "1!11", "!!1!", "1!!!!111", "!1!111!1", "!11!111"]
    )
    pick_end = random.choice(["", "OMG", "LOL", "WTF", "WTF LOL", "OMG LOL"])
    rules = {
        "YOU'RE": "UR",
        "YOUR": "UR",
        "YOU": "U",
        "WHAT THE HECK": "WTH",
        "WHAT THE HELL": "WTH",
        "WHAT THE FUCK": "WTF",
        "WHAT THE": "WT",
        "WHAT": "WUT",
        "ARE": "R",
        "WHY": "Y",
        "BE RIGHT BACK": "BRB",
        "BECAUSE": "B/C",
        "OH MY GOD": "OMG",
        "O": "OH",
        "THE": pick_the,
        "TOO": "2",
        "TO": "2",
        "BE": "B",
        "CK": "K",
        "ING": "NG",
        "PLEASE": "PLS",
        "SEE YOU": "CYA",
        "SEE YA": "CYA",
        "SCHOOL": "SKOOL",
        "AM": "M",
        "AM GOING TO": "IAM GOING TO",
        "THAT": "DAT",
        "ICK": "IK",
        "LIKE": "LIEK",
        "HELP": "HALP",
        "KE": "EK",
        "E": pick_e,
        "!": pick_ex,
        "?": pick_qt,
    }
    output = translate(string, rules) + " " + pick_end

    return output


# colors
@hook.command()
def color_parse(text: Annotated[str, Arg("Text to process")]):
    """Parse colors and formatting in the text using $(thing) syntax"""
    return colors.parse(text)


# colors - based on code by Reece Selwood - <https://github.com/hitzler/homero>
@hook.command()
def rainbow(text: Annotated[str, Arg("Text to process")]):
    """Gives the text rainbow colors."""
    text = str(text)
    text = strip(text)
    col = list(COLORS.items())
    out = ""
    l = len(COLORS)
    for i, t in enumerate(text):
        if t == " ":
            out += t
        else:
            out += col[i % l][1] + t
    return out


@hook.command()
def wrainbow(text: Annotated[str, Arg("Text to process")]):
    """Gives each word in the text rainbow colors."""
    text = str(text)
    col = list(COLORS.items())
    text = strip(text).split(" ")
    out = []
    l = len(COLORS)
    for i, t in enumerate(text):
        out.append(col[i % l][1] + t)
    return " ".join(out)


@hook.command()
def usa(text: Annotated[str, Arg("Text to process")]):
    """Makes the text more patriotic."""
    text = strip(text)
    c = [COLORS["red"], "\x0300", COLORS["blue"]]
    l = len(c)
    out = ""
    for i, t in enumerate(text):
        out += c[i % l] + t
    return out


@hook.command()
def superscript(text: Annotated[str, Arg("Text to process")]):
    """Makes the text superscript."""
    regular = "abcdefghijklmnoprstuvwxyzABDEGHIJKLMNOPRTUVW0123456789+-=()"
    super_script = "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾"
    result = []
    for char in text:
        index = regular.find(char)
        if index != -1:
            result.append(super_script[index])
        else:
            result.append(char)
    return "".join(result)


def levenshtein_distance(s1, s2):
    """Calculate the Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


@hook.command("distance", "levenshtein")
def distance_command(text: str):
    """<string1> <string2> - Calculate the Levenshtein distance between two strings. Arguments can be quoted."""
    try:
        args = shlex.split(text)
    except ValueError:
        return "Invalid arguments. Use quotes around strings with spaces."

    if len(args) != 2:
        return "Usage: .distance <string1> <string2> - Arguments can be quoted for strings with spaces."

    s1, s2 = args
    distance = levenshtein_distance(s1, s2)
    return f"Levenshtein distance between '{s1}' and '{s2}': {distance}"
