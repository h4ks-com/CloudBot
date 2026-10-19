"""
cypher.py

Ciphers and deciphers strings.

Created By:
    - Tom <https://github.com/instanceoftom>

Modified By:
    - Fletcher Boyd <https://github.com/thenoodle68>
    - Dabo Ross <https://github.com/daboross>
    - Luke Rogers <https://github.com/lukeroge>

License:
    GPL v3
"""

import base64
import binascii
from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg


def encode(password, text):
    enc = []
    for i, c in enumerate(text):
        key_c = password[i % len(password)]
        enc_c = chr((ord(c) + ord(key_c)) % 256)
        enc.append(enc_c)

    return base64.urlsafe_b64encode("".join(enc).encode()).decode()


def decode(password, encoded, event):
    dec = []
    try:
        encoded_bytes = base64.urlsafe_b64decode(encoded.encode()).decode()
    except binascii.Error:
        event.notice(f"Invalid input '{encoded}'")
        return None

    for i, c in enumerate(encoded_bytes):
        key_c = password[i % len(password)]
        dec_c = chr((256 + ord(c) - ord(key_c)) % 256)
        dec.append(dec_c)

    return "".join(dec)


@hook.command("cypher", "cipher")
def cypher(
    password: Annotated[str, Arg("Password to cypher with")],
    string: Annotated[str, Arg("Text to cypher")],
):
    """cyphers <string> with <password>"""
    return encode(password, string)


@hook.command("decypher", "decipher")
def decypher(
    password: Annotated[str, Arg("Password it was cyphered with")],
    string: Annotated[str, Arg("Text to decypher")],
    event: CommandEvent,
):
    """decyphers <string> with <password>"""
    return decode(password, string, event)
