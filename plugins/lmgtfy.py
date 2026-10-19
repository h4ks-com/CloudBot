from typing import Annotated
from urllib.parse import quote

from cloudbot import hook
from cloudbot.hook import Arg
from cloudbot.util import web


@hook.command("lmgtfy", "gfy")
def lmgtfy(phrase: Annotated[str, Arg("What to search for")]):
    """gets a lmgtfy.com link for the specified phrase"""

    link = f"https://letmegooglethat.com/?q={quote(phrase)}"

    return web.try_shorten(link)
