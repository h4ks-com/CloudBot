from requests import HTTPError

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.util.web import get_session


def get_data(url, reply, bot, params=None):
    try:
        with get_session().get(
            url, headers={"User-Agent": bot.user_agent}, params=params
        ) as r:
            r.raise_for_status()
    except HTTPError:
        reply("API error occurred.")
        raise

    return r


@hook.command(autohelp=False)
def cats(event: CommandEvent, bot: CloudBot):
    """- gets a fucking fact about cats."""
    r = get_data(
        "https://catfact.ninja/fact",
        event.reply,
        bot,
        params={"max_length": 100},
    )
    json = r.json()
    response = json["fact"]
    return response


@hook.command(autohelp=False)
def catgifs(event: CommandEvent, bot: CloudBot):
    """- gets a fucking cat gif."""
    r = get_data("http://marume.herokuapp.com/random.gif", event.reply, bot)
    return f"OMG A CAT GIF: {r.url}"
