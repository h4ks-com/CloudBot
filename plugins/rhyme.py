import json
import urllib.parse
from typing import Annotated

from cloudbot import hook
from cloudbot.hook import Arg
from cloudbot.util.web import get_session

API = "https://api.datamuse.com/words?"
MAX_DISPLAY_WORDS = 20


def get(**params):
    r = get_session().get(
        API
        + "&".join(
            [
                f"{key}={urllib.parse.quote(value)}"
                for key, value in params.items()
            ]
        )
    )
    if r.status_code != 200:
        return None, r.status_code
    return json.loads(r.content), r.status_code


def words(**params):
    r, code = get(**params)
    if r is None:
        return "Request failed. Status code: " + code
    return ", ".join([w["word"] for w in r[:MAX_DISPLAY_WORDS]])


@hook.command()
def rhyme(word: Annotated[str, Arg("Word to rhyme with")]):
    """get rhymes for the word."""
    return words(rel_rhy=word.split()[0])


@hook.command()
def adj(word: Annotated[str, Arg("Word to describe")]):
    """get adjectives that are often used to describe input word."""
    return words(rel_jjb=word.split()[0])


@hook.command()
def noun(adjective: Annotated[str, Arg("Adjective that describes the nouns")]):
    """nouns that are often described by the adjective."""
    return words(rel_jja=adjective.split()[0])


@hook.command()
def soundlike(word: Annotated[str, Arg("Word to match by sound")]):
    """words that sound like the word."""
    return words(sl=word.split()[0])


@hook.command()
def mean(meaning: Annotated[str, Arg("Meaning to match")]):
    """words with a meaning similar to the text."""
    return words(ml=meaning)


@hook.command()
def rhymerel(
    word1: Annotated[str, Arg("Word to rhyme with")],
    word2: Annotated[str, Arg("Word the rhymes relate to")],
):
    """words that rhyme with word1 that are related to word2."""
    return words(ml=word1, rel_rhy=word1)


@hook.command()
def adjrel(
    word1: Annotated[str, Arg("Word to describe")],
    word2: Annotated[str, Arg("Word the adjectives relate to")],
):
    """adjectives describing word1 sorted by how related they are to word2."""
    return words(rel_jjb=word1, topics=word2.split()[0])
