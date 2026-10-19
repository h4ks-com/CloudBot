# dict.cc interface
#
# dict.c bot: pip install dict.cc.py

import logging
from typing import Annotated

from dictcc import AVAILABLE_LANGUAGES, Dict

from cloudbot import hook
from cloudbot.hook import Arg

next_msg = "  ----- Type `.tn` for more translations"
logger = logging.getLogger("cloudbot")

last_results = {}  # per user results
translator = Dict()


@hook.command("t")
def dcc(
    nick: str,
    lang_in: Annotated[str, Arg("Two-letter input language code")],
    lang_out: Annotated[str, Arg("Two-letter output language code")],
    query: Annotated[str, Arg("Text to translate")],
):
    """translate <query> from <lang_in> to <lang_out> where <lang_in> and <lang_out> are two-letter language codes"""
    query = " ".join(query.split())

    if lang_in not in AVAILABLE_LANGUAGES:
        return f"Invalid input language: {lang_in}"
    if lang_out not in AVAILABLE_LANGUAGES:
        return f"Invalid output language: {lang_out}"

    results = translator.translate(
        query, from_language=lang_in, to_language=lang_out
    )
    logger.info(results)
    if len(results.translation_tuples) == 0:
        return "No transations were found!"
    result = results.translation_tuples.pop(0)
    last_results[nick] = results
    return f"\x02{results.from_lang.strip()}\x02: {result[0]} --> \x02{results.to_lang}\x02: {result[1]}{next_msg if len(last_results[nick].translation_tuples) > 0 else ''}"


@hook.command("tn")
def dcc_next(text: str, nick: str):
    """- Show the next translation"""
    results = last_results[nick]
    result = results.translation_tuples.pop(0)
    if last_results:
        return f"\x02{results.from_lang.strip()}\x02: {result[0]} --> \x02{results.to_lang}\x02: {result[1]}{next_msg if len(last_results[nick].translation_tuples) > 0 else ''}"
    else:
        return "No translations left!"
