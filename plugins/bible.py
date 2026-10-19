from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting
from cloudbot.util.web import get_session


@hook.command("bible", "passage", singlethread=True)
def bible(
    passage: Annotated[str, Arg("Passage to look up")], event: CommandEvent
):
    """Prints the specified passage from the Bible"""
    passage = passage.strip()
    params = {"passage": passage, "formatting": "plain", "type": "json"}
    try:
        r = get_session().get(
            "https://labs.bible.org/api/",
            params=params,
        )
        r.raise_for_status()
        response = r.json()[0]
    except Exception as e:
        event.reply(
            formatting.truncate(
                "Something went wrong, either you entered an invalid passage or the API is down",
                400,
            )
        )
        raise e

    book = response["bookname"]
    ch = response["chapter"]
    ver = response["verse"]
    txt = response["text"]
    return f"\x02{book} {ch}:{ver}\x02 {txt}"
