"""formatting.py.

Contains functions for formatting and working with strings.

The licensing for this module isn't solid, because I started working on this module before I had a proper
system for tracking code licences. If your code is in this file and you have any queries, contact me by
email at <lukeroge@gmail.com>!

Maintainer:
    - Luke Rogers <https://github.com/lukeroge>

License:
    GPL v3

License for final section (all code after the "DJANGO LICENCE" comment):
    BSD license

    Copyright (c) Django Software Foundation and individual contributors.
    All rights reserved.

    Redistribution and use in source and binary forms, with or without modification,
    are permitted provided that the following conditions are met:

        1. Redistributions of source code must retain the above copyright notice,
           this list of conditions and the following disclaimer.

        2. Redistributions in binary form must reproduce the above copyright
           notice, this list of conditions and the following disclaimer in the
           documentation and/or other materials provided with the distribution.

        3. Neither the name of Django nor the names of its contributors may be used
           to endorse or promote products derived from this software without
           specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
    ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
    WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
    ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
    (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
    LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
    ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
    SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import copy
import re
import warnings
from html.parser import HTMLParser

from bs4 import BeautifulSoup

from cloudbot.util.colors import get_color, strip_irc

# Constants

IRC_COLOR_RE = re.compile(r"(\x03(\d+,\d+|\d)|[\x0f\x02\x16\x1f])")

REPLACEMENTS = {
    "a": "ä",
    "b": "Б",
    "c": "ċ",
    "d": "đ",
    "e": "ë",
    "f": "ƒ",
    "g": "ġ",
    "h": "ħ",
    "i": "í",
    "j": "ĵ",
    "k": "ķ",
    "l": "ĺ",
    "m": "ṁ",
    "n": "ñ",
    "o": "ö",
    "p": "ρ",
    "q": "ʠ",
    "r": "ŗ",
    "s": "š",
    "t": "ţ",
    "u": "ü",
    "v": "",
    "w": "ω",
    "x": "χ",
    "y": "ÿ",
    "z": "ź",
    "A": "Å",
    "B": "Β",
    "C": "Ç",
    "D": "Ď",
    "E": "Ē",
    "F": "Ḟ",
    "G": "Ġ",
    "H": "Ħ",
    "I": "Í",
    "J": "Ĵ",
    "K": "Ķ",
    "L": "Ĺ",
    "M": "Μ",
    "N": "Ν",
    "O": "Ö",
    "P": "Р",
    "Q": "Ｑ",
    "R": "Ŗ",
    "S": "Š",
    "T": "Ţ",
    "U": "Ů",
    "V": "Ṿ",
    "W": "Ŵ",
    "X": "Χ",
    "Y": "Ỳ",
    "Z": "Ż",
}

IRC_TAGS = {
    "b": "\x02",  # Bold
    "i": "\x1D",  # Italic
    "u": "\x1F",  # Underline
}
# Classes


class HTMLTextExtractor(HTMLParser):
    """Takes HTML and provides cleaned and stripped text."""

    def __init__(self):
        HTMLParser.__init__(self)
        self.result = []

    def handle_data(self, d):
        self.result.append(d)

    def get_text(self):
        return "".join(self.result)


# Functions


def strip_html(to_strip):
    """Takes HTML and returns cleaned and stripped text."""
    s = HTMLTextExtractor()
    s.feed(to_strip)
    return s.get_text()


def munge(text, count=0):
    """Replaces characters in a string with visually similar characters to
    avoid pinging users in IRC.

    Count sets how many characters are replaced, defaulting to all
    characters.
    """
    reps = 0
    for n, c in enumerate(text):
        rep = REPLACEMENTS.get(c)
        if rep:
            text = text[:n] + rep + text[n + 1 :]
            reps += 1
            if reps == count:
                break
    return text


def ireplace(text, old, new, count=None):
    """A case-insensitive replace() clone.

    Return a copy of text with all occurrences of substring old replaced
    by new. If the optional argument count is given, only the first
    count occurrences are replaced.
    """
    pattern = re.compile(re.escape(old), re.IGNORECASE)

    if count:
        return pattern.sub(new, text, count=count)

    return pattern.sub(new, text)


def multi_replace(text, word_dic):
    """Takes a string and replace words that match a key in a dictionary with
    the associated value, then returns the changed text."""
    rc = re.compile("|".join(map(re.escape, word_dic)))

    def translate(match):
        return word_dic[match.group(0)]

    return rc.sub(translate, text)


# compatibility
multiword_replace = multi_replace


def truncate_words(content, length=10, suffix="..."):
    """Truncates a string after a certain number of words."""
    split = content.split()
    if len(split) <= length:
        return " ".join(split[:length])

    return " ".join(split[:length]) + suffix


def truncate(
    content: str, length: int = 100, suffix: str = "...", sep: str = " "
) -> str:
    """Truncates a string after a certain number of characters.

    Function always tries to truncate on a word boundary.
    """
    if len(content) <= length:
        return content

    return content[:length].rsplit(sep, 1)[0] + suffix


# compatibility
truncate_str = truncate
strip_colors = strip_irc


def chunk_str(content, length=420):
    """Chunks a string into smaller strings of given length.

    Returns chunks.
    """

    def chunk(c, l):
        while c:
            out = (c + " ")[:l].rsplit(" ", 1)[0]
            c = c[len(out) :].strip()
            yield out

    return list(chunk(content, length))


def pluralize(num=0, text=""):  # pragma: no cover
    """Takes a number and a string, and pluralizes that string using the number
    and combines the results."""
    warnings.warn(
        "formatting.pluralize() is deprecated, please use one of the other formatting.pluralize_*() functions",
        DeprecationWarning,
    )
    return pluralize_suffix(num, text)


def pluralise(num=0, text=""):  # pragma: no cover
    """Takes a number and a string, and pluralizes that string using the number
    and combines the results."""
    warnings.warn(
        "formatting.pluralise() is deprecated, please use one of the other formatting.pluralise_*() functions",
        DeprecationWarning,
    )
    return pluralise_suffix(num, text)


def pluralize_suffix(num=0, text="", suffix="s"):
    """Takes a number and a string, and pluralizes that string using the number
    and combines the results."""
    return pluralize_select(num, text, text + suffix)


pluralise_suffix = pluralize_suffix


def pluralize_select(count, single, plural):
    return f"{count:,} {single if count == 1 else plural}"


pluralise_select = pluralize_select


def pluralize_auto(count, thing):
    if thing.endswith("us"):
        return pluralize_select(count, thing, thing[:-2] + "i")

    if thing.endswith("is"):
        return pluralize_select(count, thing, thing[:-2] + "es")

    if thing.endswith(("s", "ss", "sh", "ch", "x", "z")):
        return pluralize_suffix(count, thing, "es")

    if thing.endswith(("f", "fe")):
        return pluralize_select(count, thing, thing.rsplit("f", 1)[0] + "ves")

    if thing.endswith("y") and thing[-2:-1].lower() not in "aeiou":
        return pluralize_select(count, thing, thing[:-1] + "ies")

    if thing.endswith("y") and thing[-2:-1].lower() in "aeiou":
        return pluralize_suffix(count, thing)

    if thing.endswith("o"):
        return pluralize_suffix(count, thing, "es")

    if thing.endswith("on"):
        return pluralize_select(count, thing, thing[:-2] + "a")

    return pluralize_suffix(count, thing)


pluralise_auto = pluralize_auto


def dict_format(args, formats):
    matches: dict[str, int] = {}
    for f in formats:
        try:
            # Check if values can be mapped
            m = f.format(**args)
            # Insert match and number of matched values (max matched values if already in dict)
            matches[m] = max(
                [matches.get(m, 0), len(re.findall(r"({.*?\})", f))]
            )
        except KeyError:
            continue

    # Return most complete match, ranked by values matched and then my match length or None
    if not matches:
        return None

    return max(matches.items(), key=lambda x: (x[1], len(x[0])))[0]


# DJANGO LICENCE

split_re = re.compile(
    r"""((?:[^\s'"]*(?:(?:"(?:[^"\\]|\\.)*" | '(?:["""
    r"""^'\\]|\\.)*')[^\s'"]*)+) | \S+)""",
    re.VERBOSE,
)


def smart_split(text):
    r"""
    Generator that splits a string by spaces, leaving quoted phrases together.
    Supports both single and double quotes, and supports escaping quotes with
    backslashes. In the output, strings will keep their initial and trailing
    quote marks and escaped quotes will remain escaped (the results can then
    be further processed with unescape_string_literal()).

    >>> list(smart_split(r'This is "a person\'s" test.'))
    ['This', 'is', '"a person\\\'s"', 'test.']
    >>> list(smart_split(r"Another 'person\'s' test."))
    ['Another', "'person\\'s'", 'test.']
    >>> list(smart_split(r'A "\"funky\" style" test.'))
    ['A', '"\\"funky\\" style"', 'test.']
    """
    for bit in split_re.finditer(text):
        yield bit.group(0)


def get_text_list(list_, last_word="or"):
    """
    >>> get_text_list(['a', 'b', 'c', 'd'])
    'a, b, c or d'
    >>> get_text_list(['a', 'b', 'c'], 'and')
    'a, b and c'
    >>> get_text_list(['a', 'b'], 'and')
    'a and b'
    >>> get_text_list(['a'])
    'a'
    >>> get_text_list([])
    ''
    """
    if not list_:
        return ""

    if len(list_) == 1:
        return list_[0]

    return "{} {} {}".format(
        # Translators: This string is used as a separator between list elements
        ", ".join([i for i in list_][:-1]),
        last_word,
        list_[-1],
    )


def gen_markdown_table(headers, rows):
    """Generates a Markdown formatted table from the data."""
    rows = copy.copy(rows)
    rows.insert(0, headers)
    rotated = zip(*reversed(rows))

    sizes = tuple(map(lambda l: max(max(map(len, l)), 3), rotated))
    rows.insert(1, tuple(("-" * size) for size in sizes))
    lines = [
        "| {} |".format(
            " | ".join(cell.ljust(sizes[i]) for i, cell in enumerate(row))
        )
        for row in rows
    ]
    return "\n".join(lines)


def json_format(
    dict_obj: dict | list, indent: int = 2, max_elements: int = 20
) -> list[str]:
    """Generates a multi-line JSON formatted string from the data to be
    displayed in IRC showing keys and values and identing."""
    base_types = (int, float, str, bool, type(None))

    def format_base_type(value):
        if value is None:
            color = get_color("red")
            value = f"{color}null{color}"
        elif isinstance(value, bool):
            color = get_color("true" if value else "red")
            value = f"{color}{str(value).lower()}{color}"
        elif isinstance(value, (int, float)):
            color = get_color("lime" if isinstance(value, int) else "cyan")
            value = f"{color}\x1d{value}\x1d{color}"
        elif isinstance(value, str):
            color = get_color("dark_green")
            value = f"{color}{value}{color}"
        else:
            value = str(value)

        return value

    def get_key_value_line(key, value, identation_level) -> str:
        return f"{' ' * identation_level * indent}\x02{key}\x02 -> {value}"

    def highlight_flat_obj(
        obj: dict | list, identation_level: int = 0
    ) -> list[str]:
        """Make keys bold, numbers italic and string values normal with green
        foreground Null and false values are red and true values are blue."""
        obj_lines = []
        elements = enumerate(obj) if isinstance(obj, list) else obj.items()
        for key, value in elements:
            if isinstance(value, base_types):
                value = format_base_type(value)
                obj_lines.append(
                    get_key_value_line(key, value, identation_level)
                )
            elif isinstance(value, list):
                obj_lines.append(get_key_value_line(key, "", identation_level))
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        obj_lines.append(
                            get_key_value_line(i, "", identation_level + 1)
                        )
                        item = highlight_flat_obj(item, identation_level + 2)
                        obj_lines.extend(item)
                    elif isinstance(item, list):
                        obj_lines.append(
                            get_key_value_line(i, "", identation_level + 1)
                        )
                        item = highlight_flat_obj(item, identation_level + 2)
                        obj_lines.extend(item)
                    else:
                        value = format_base_type(value)
                        obj_lines.append(
                            get_key_value_line(key, value, identation_level)
                        )
            elif isinstance(value, dict):
                obj_lines.extend(
                    highlight_flat_obj(value, identation_level + 1)
                )
            else:
                value = str(value)
                obj_lines.append(
                    get_key_value_line(key, value, identation_level)
                )

        return obj_lines

    return [truncate(c, 420) for c in highlight_flat_obj(dict_obj)][
        0:max_elements
    ]


def html_to_irc(html: str) -> str:
    # Parse the HTML
    soup = BeautifulSoup(html, "html.parser")

    # Recursive function to convert tags to IRC
    def process_element(element):
        # If the element is a string (text), return it as is
        if isinstance(element, str):
            return element

        # Handle <div> specifically by adding " - " between its contents
        if element.name == "div":
            # Process the content of the <div> and join with " - "
            div_content = " - ".join(
                [process_element(child) for child in element.children]
            )
            return div_content

        # If it's a tag, wrap content with the corresponding IRC control codes
        if element.name in IRC_TAGS:
            start_code = IRC_TAGS[element.name]
            end_code = IRC_TAGS[element.name]
            inner_text = "".join(
                [process_element(child) for child in element.children]
            )
            return f"{start_code}{inner_text}{end_code}"
        else:
            # If the tag is unknown or not in the map, process its children normally
            return "".join(
                [process_element(child) for child in element.children]
            )

    # Process the entire document
    irc_text = process_element(soup)

    # Return the IRC-formatted text
    return str(irc_text)
