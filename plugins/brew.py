from typing import Annotated

from requests import HTTPError

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util.web import get_session

api_url = "http://api.brewerydb.com/v2/search?format=json"


@hook.command("brew")
def brew(query: Annotated[str, Arg("Beer to search for")], event: CommandEvent):
    """returns the first brewerydb search result for <query>"""
    api_key = bot.config.get_api_key("brewerydb")
    if not api_key:
        return "No brewerydb API key set."

    params = {"key": api_key, "type": "beer", "withBreweries": "Y", "q": query}
    request = get_session().get(api_url, params=params)

    try:
        request.raise_for_status()
    except HTTPError:
        event.reply(f"Failed to fetch info ({request.status_code})")
        raise

    response = request.json()

    output = "No results found."

    try:
        if "totalResults" in response:
            if response["totalResults"] == 0:
                return output

            beer = response["data"][0]
            brewery = beer["breweries"][0]

            style = "unknown style"
            if "style" in beer:
                style = beer["style"]["shortName"]

            abv = "?.?"
            if "abv" in beer:
                abv = beer["abv"]

            url = "[no website found]"
            if "website" in brewery:
                url = brewery["website"]

            content = {
                "name": beer["nameDisplay"],
                "style": style,
                "abv": abv,
                "brewer": brewery["name"],
                "url": url,
            }

            output = "{name} by {brewer} ({style}, {abv}% ABV) - {url}".format(
                **content
            )

    except Exception:
        event.reply("Error parsing results.")
        raise

    return output
