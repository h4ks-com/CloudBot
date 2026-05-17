from dataclasses import dataclass

import requests

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session

# Define some constants
base_url = "https://maps.googleapis.com/maps/api/"
geocode_api = base_url + "geocode/json"

# Change this to a ccTLD code (eg. uk, nz) to make results more targeted towards that specific country.
# <https://developers.google.com/maps/documentation/geocoding/#RegionCodes>
bias = None


def check_status(status):
    """A little helper function that checks an API error code and returns a nice message.
    Returns None if no errors found"""
    if status == "REQUEST_DENIED":
        return "The geocode API is off in the Google Developers Console."

    if status == "ZERO_RESULTS":
        return "No results found."

    if status == "OVER_QUERY_LIMIT":
        return "The geocode API quota has run out."

    if status == "UNKNOWN_ERROR":
        return "Unknown Error."

    if status == "INVALID_REQUEST":
        return "Invalid Request."

    if status == "OK":
        return None

    return None


class GeolocationException(Exception):
    pass


@dataclass
class GoogleLocation:
    lat: float
    lng: float
    url: str
    tags: str
    location_name: str
    country: str

    def __str__(self):
        return f"\x02{self.location_name}\x02 - {self.url} ({self.tags})"

    @staticmethod
    def _from_api_response(response: requests.Response) -> "GoogleLocation":
        json = response.json()

        error = check_status(json["status"])
        if error:
            raise GeolocationException(error)

        result = json["results"][0]

        location_name = result["formatted_address"]
        location = result["geometry"]["location"]
        formatted_location = "{lat},{lng},16z".format(**location)
        countries = [
            x["long_name"]
            for x in result["address_components"]
            if "country" in x["types"]
        ]
        if not countries:
            country = ""
        else:
            country = countries[0]

        url = (
            "https://google.com/maps/@" + formatted_location + "/data=!3m1!1e3"
        )
        tags = result["types"]

        # if 'political' is not the only tag, remove it.
        if not tags == ["political"]:
            tags = [x for x in result["types"] if x != "political"]

        tags = ", ".join(tags).replace("_", " ")
        return GoogleLocation(
            location["lat"], location["lng"], url, tags, location_name, country
        )

    @staticmethod
    def from_address(text: str, dev_key: str) -> "GoogleLocation":
        # Use the Geocoding API to get co-ordinates from the input
        params = {"address": text, "key": dev_key}
        if bias:
            params["region"] = bias

        return GoogleLocation._from_api_response(
            get_session().get(geocode_api, params=params)
        )

    @staticmethod
    def from_lat_lng(lat: float, lng: float, dev_key: str) -> "GoogleLocation":
        # Use the Geocoding API to get co-ordinates from the input
        params = {"latlng": f"{lat},{lng}", "key": dev_key}
        if bias:
            params["region"] = bias

        return GoogleLocation._from_api_response(
            get_session().get(geocode_api, params=params)
        )


@hook.command("locate", "maps")
def locate(text):
    """<location> - Finds <location> on Google Maps."""
    api_key = bot.config.get_api_key("google")
    if not api_key:
        return "This command requires a Google API key."

    try:
        location = GoogleLocation.from_address(text, api_key)
    except GeolocationException as e:
        return str(e)

    return str(location)
