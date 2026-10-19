from typing import Annotated

from enchant import Dict
from enchant.checker import SpellChecker
from enchant.tokenize import EmailFilter, URLFilter

from cloudbot import hook
from cloudbot.hook import Arg

locale = "en_US"
en_dict = Dict(locale)


@hook.command()
def spell(phrase: Annotated[str, Arg("Word or sentence to check")]):
    """Check spelling of a word or sentence."""
    if len(phrase.split(" ")) > 1:
        # input is a sentence
        checker = SpellChecker(en_dict, filters=[EmailFilter, URLFilter])
        checker.set_text(phrase)

        offset = 0
        for err in checker:
            # find the location of the incorrect word
            start = err.wordpos + offset
            finish = start + len(err.word)
            # get some suggestions for it
            suggestions = err.suggest()
            s_string = "/".join(suggestions[:3])
            s_string = f"\x02{s_string}\x02"
            # calculate the offset for the next word
            offset = (offset + len(s_string)) - len(err.word)
            # replace the word with the suggestions
            phrase = phrase[:start] + s_string + phrase[finish:]
        return phrase
    else:
        # input is a word
        is_correct = en_dict.check(phrase)
        suggestions = en_dict.suggest(phrase)
        s_string = ", ".join(suggestions[:10])
        if is_correct:
            return (
                f'"{phrase}" appears to be \x02valid\x02! '
                f"(suggestions: {s_string})"
            )
        else:
            return (
                f'"{phrase}" appears to be \x02invalid\x02! '
                f"(suggestions: {s_string})"
            )
