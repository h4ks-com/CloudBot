from unittest.mock import MagicMock

import pytest

from cloudbot.util.command_args import CommandArgError, build_spec
from plugins import cypher


@pytest.mark.parametrize(
    "plaintext,key,ciphertext",
    [
        ("some input", "supersecretkey", "w6bDpMOdw4rCksOcw5PDk8Onw5k="),
        (
            "other input and stuff",
            "supersecretkey234",
            "w6LDqcOYw4rDpMKTw47DkcOiw5rDqMKLw4bDp8KWU8Knw6fDqsOWw4s=",
        ),
    ],
)
def test_encipher(plaintext, key, ciphertext):
    assert cypher.cypher(key, plaintext) == ciphertext


@pytest.mark.parametrize(
    "ciphertext,key,plaintext",
    [
        ("w6bDpMOdw4rCksOcw5PDk8Onw5k=", "supersecretkey", "some input"),
        (
            "w6LDqcOYw4rDpMKTw47DkcOiw5rDqMKLw4bDp8KWU8Knw6fDqsOWw4s=",
            "supersecretkey234",
            "other input and stuff",
        ),
    ],
)
def test_decipher(ciphertext, key, plaintext):
    event = MagicMock()
    assert cypher.decypher(key, ciphertext, event) == plaintext


def test_base64_error():
    event = MagicMock()
    assert cypher.decypher("thing", "stuff and things", event) is None

    event.notice.assert_called_with("Invalid input 'stuff and things'")


@pytest.mark.parametrize("func", [cypher.cypher, cypher.decypher])
def test_param_error(func):
    spec = build_spec(func)
    assert spec.parse_text("key some text") == {
        "password": "key",
        "string": "some text",
    }
    with pytest.raises(CommandArgError):
        spec.parse_text("")
    with pytest.raises(CommandArgError):
        spec.parse_text("key")
