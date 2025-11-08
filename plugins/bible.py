from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.web import get_session


@hook.command("bible", "passage", singlethread=True)
def bible(text, reply):
    """<passage> - Prints the specified passage from the Bible"""
    passage = text.strip()
    params = {"passage": passage, "formatting": "plain", "type": "json"}
    try:
        r = get_session().get(
            "https://labs.bible.org/api/",
            params=params,
            impersonate="chrome124",
        )
        r.raise_for_status()
        response = r.json()[0]
    except HTTPError as e:
        reply(
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
