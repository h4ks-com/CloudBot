from bs4 import BeautifulSoup

from cloudbot import hook
from cloudbot.util.web import get_session


@hook.command("ruad", "rud", "ruadick")
def RUADICK(text, message):
    """<username> - checks ruadick.com to see if you're a dick on reddit"""
    DickCheck = text.strip()
    dickstatus = get_session().get(f"http://www.ruadick.com/user/{DickCheck}")
    dickstatus.raise_for_status()
    DickSoup = BeautifulSoup(dickstatus.content, "lxml")
    Dickstr = str(DickSoup.h2)

    dickstrip = Dickstr.lstrip("<h2>").rstrip("</h2>")

    if dickstrip == "None":
        message("I can't find that user")
    else:
        message(f"{dickstrip} {dickstatus.url}")
