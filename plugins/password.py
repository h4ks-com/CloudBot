import random as std_random
import string
from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg

try:
    from Crypto.Random import random

    gen = random.StrongRandom()
except ImportError:
    random = None
    # Just use the regular random module, not the strong one
    gen = std_random.SystemRandom()

with open("data/password_words.txt", encoding="utf-8") as f:
    common_words = [line.strip() for line in f.readlines()]


@hook.command(autohelp=False)
def password(text: str, event: CommandEvent):
    """[length [types]] - generates a password of <length> (default 12).
    [types] can include 'alpha', 'no caps', 'numeric', 'symbols' or any combination: eg. 'numbers symbols'
    (default: alpha numeric no caps)"""
    okay = ""

    # find the length needed for the password
    numb = text.split(" ")

    try:
        length = int(numb[0])
    except ValueError:
        length = 12

    if length > 50:
        event.notice("Maximum length is 50 characters.")
        return

    # add alpha characters
    if "alpha" in text or "letter" in text:
        okay += string.ascii_lowercase
        # adds capital characters if not told not to
        if "no caps" not in text:
            okay += string.ascii_uppercase

    # add numbers
    if "numeric" in text or "number" in text:
        okay += string.digits

    # add symbols
    if "symbol" in text or "special" in text:
        okay += string.punctuation

    # defaults to lowercase alpha + numbers password if the okay string is empty
    if not okay:
        okay = string.ascii_lowercase + string.digits

    _okay = list(okay)

    # extra random lel
    gen.shuffle(_okay)
    chars = []

    for _ in range(length):
        chars.append(gen.choice(_okay))

    event.notice("".join(chars))


@hook.command("wpass", "wordpass", "wordpassword", autohelp=False)
def word_password(
    event: CommandEvent,
    length: Annotated[int, Arg("How many words, at most 10")] = 3,
):
    """generates an easy to remember password with [length] (default 3) commonly used words"""
    if length > 10:
        event.notice("Maximum length is 50 characters.")
        return

    words = []
    # generate password
    for _ in range(length):
        words.append(gen.choice(common_words))

    event.notice(
        "Your password is '{}'. Feel free to remove the spaces when using it.".format(
            " ".join(words)
        )
    )
