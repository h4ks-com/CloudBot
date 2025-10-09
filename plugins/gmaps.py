import json
import random
import re
import shlex
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta

import googlemaps
import pytz
import requests
from fuzzywuzzy import fuzz
from PIL.Image import Image
from pyproj import Geod
from streetview import get_streetview, search_panoramas

from cloudbot import hook
from cloudbot.util.web import get_session
from cloudbot.util import timeformat
from cloudbot.util.formatting import html_to_irc
from plugins.huggingface import FileIrcResponseWrapper
from plugins.locate import GeolocationException, GoogleLocation

MAX_HOURLY_REQUESTS = 30
MAX_OUTPUT_LINES = 10
MIN_GUESS_GAME_DURATION = 60  # seconds

# "driving", "walking", "bicycling" or "transit"

modes_map = {
    "driving": "driving",
    "car": "driving",
    "drive": "driving",
    "walking": "walking",
    "walk": "walking",
    "bicycling": "bicycling",
    "bike": "bicycling",
    "transit": "transit",
    "public": "transit",
    "bus": "transit",
    "train": "transit",
}
modes = ", ".join(f'"{m}"' for m in set(modes_map.values()))

emoji_map = {
    "driving": "🚗",
    "walking": "🚶",
    "bicycling": "🚲",
    "transit": "🚆",
}

lat_lng_re = re.compile(r"^\s*(-?\d+\.\d+),\s*(-?\d+\.\d+)\s*$")

last_hour_usages = []


def ratelimit():
    global last_hour_usage

    now = datetime.now(pytz.timezone("UTC"))

    for i, usage in enumerate(last_hour_usages):
        if usage < now - timedelta(hours=1):
            last_hour_usages.pop(i)
        else:
            break

    if len(last_hour_usages) >= MAX_HOURLY_REQUESTS:
        return True

    last_hour_usages.append(now)
    return False


@hook.command("gd", "gmd", "directions", autohelp=False)
def directions(text, event, reply, bot, nick, chan):
    """<type> <origin> to <destination> - Get directions from Google Maps"""
    if ratelimit():
        return "Too many requests. Please try again later."

    api_key = bot.config.get("api_keys", {}).get("google", None)
    try:
        gmaps = googlemaps.Client(key=api_key)
    except ValueError:
        return "No or wrong Google API key configured."

    text = text.strip()
    if not text:
        return "Usage: gd [mode] <origin> to <destination>"

    usage_msg = "Usage: gd [mode] <origin> to <destination>"

    args = shlex.split(text)
    if '"' in text:
        if len(args) == 4:
            mode = args[0].lower()
            if mode not in modes_map:
                return f"Unknown mode: '{mode}'. Possible modes: {modes}"
            mode = modes_map[mode]
            points = " ".join(args[1:]).split(" to ", 1)
        elif len(args) == 3:
            mode = None
            points = text.split(" to ")
        else:
            return usage_msg
    else:
        args = text.split(" ")[0]
        mode = args.lower().strip()
        if mode in modes_map:
            mode = modes_map[mode]
            points = " ".join(args).strip().split(" to ", 1)
        else:
            mode = None
            points = text.split(" to ", 1)

    if len(points) != 2:
        return usage_msg

    reply(
        f"🔍 Searching directions from '{points[0]}' to '{points[1]}' using '{mode or 'all'}'"
    )
    now = datetime.now(pytz.timezone("UTC"))
    last_hour_usages.append(now)

    try:
        directions = gmaps.directions(
            points[0],
            points[1],
            mode=mode or "transit",
            departure_time=now,
            alternatives=True,
        )
    except googlemaps.exceptions.ApiError as e:
        return f"Error: {e}"

    if not directions:
        return "No results found."

    best_route = directions[0]

    i = 0
    for leg in best_route["legs"]:
        duration = leg["duration"]["text"]
        distance = leg["distance"]["text"]
        from_ = leg["start_address"]
        to = leg["end_address"]
        reply(f"📍 \x02{from_}  -  {to} ({distance}, {duration})")
        for step in leg["steps"]:
            # Only limit in channels
            if i >= MAX_OUTPUT_LINES and chan.startswith("#"):
                reply(
                    f"🔽 More steps: {len(leg['steps']) - i}. Repeat the command in a private message for full details."
                )
                break
            mode = step["travel_mode"].lower()
            emoji = emoji_map.get(mode, "🚶")
            distance = step.get("distance", {}).get("text", "")
            msg = f"    {emoji} {distance}: {html_to_irc(step['html_instructions'])}"
            if mode == "transit":
                arrival_time = (
                    step.get("transit_details", {})
                    .get("arrival_time", {})
                    .get("text", "")
                )
                headsign = step.get("transit_details", {}).get("headsign", "")
                num_stops = step.get("transit_details", {}).get("num_stops", "")
                msg += f" - {headsign} ({arrival_time}) - {num_stops} stops"
            reply(msg)

            i += 1

        if i >= MAX_OUTPUT_LINES:
            break


def upload_image(image: Image) -> str:
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        image.save(f.name)
        image_url = FileIrcResponseWrapper.upload_file(f.name, "st")
    return image_url


@hook.command("sv", "streetview", autohelp=False)
def streetview(text, reply, bot):
    """<location> - Get a street view image from Google Maps. Possible parameters are: fov, heading, pitch, width, height, move (meters), move_heading - e.g. 'sv 40.7128, -74.0060 fov:120'"""
    text = text.strip()
    if not text:
        return "Usage: sv <location>"

    api_key = bot.config.get("api_keys", {}).get("google", None)
    if not api_key:
        return "This command requires a Google API key."

    if ratelimit():
        return "Too many requests. Please try again later."

    # Parameters have the format key:value
    params: dict[str, int] = {}
    for param in re.findall(r"\b(\w+):(-?\d+\.?\d*)\b", text):
        key, value = param
        if key in [
            "fov",
            "heading",
            "pitch",
            "width",
            "height",
            "move",
            "move_heading",
        ]:
            text = text.replace(f"{key}:{value}", "")
            try:
                params[key] = int(value)
            except ValueError:
                return f"Invalid value for parameter '{key}'. Must be a integer number."
        else:
            return f"Invalid parameter: {key}"

    text = text.strip()

    if re.match(lat_lng_re, text):
        lat, lng = map(float, text.split(","))
        try:
            location = GoogleLocation.from_lat_lng(lat, lng, api_key)
            location_name = location.location_name
        except GeolocationException as e:
            location_name = f"{lat}, {lng}"
    else:
        try:
            location = GoogleLocation.from_address(text, api_key)
        except GeolocationException as e:
            return str(e)
        location_name = location.location_name
        lat, lng = location.lat, location.lng

    if "move" in params:
        distance = float(params.pop("move"))
        move_heading = params.get("move_heading", params.get("heading", 0))
        move_heading = float(move_heading)

        # Get new lat, lng in that direction
        geod = Geod(ellps="WGS84")
        lng, lat, _ = geod.fwd(lng, lat, move_heading, distance)

    if "move_heading" in params:
        params.pop("move_heading")

    panos = search_panoramas(lat=lat, lon=lng)

    if not panos:
        return "No panoramas found for this location."

    pano = panos[0]
    streetview = get_streetview(pano.pano_id, api_key=api_key, **params)
    image_url = upload_image(streetview)

    return f"📸 {location_name} - {image_url}"


@dataclass
class GuessGame:
    location: GoogleLocation
    start_time: datetime
    image_url: str


guess_games: "dict[str, GuessGame]" = {}


def new_guess_game(bot, chan) -> str:
    api_key = bot.config.get("api_keys", {}).get("google", None)
    if not api_key:
        return "This command requires a Google API key."

    if ratelimit():
        return "Too many requests. Please try again later."

    def get_random_land_location() -> GoogleLocation:
        countries: "dict[str, str]" = json.loads(
            open("plugins/ISO3166-1.alpha2.json").read()
        )
        country_code = random.choice(list(countries.keys()))
        country_name = countries[country_code]

        api_url = f"https://api.3geonames.org/randomland.{country_code}.json"
        r = get_session().get(api_url)
        r.raise_for_status()
        try:
            response_json = r.json()
        except json.JSONDecodeError:
            raise ValueError("API Overloaded. Please try again later.")
        lat = float(response_json["nearest"]["latt"])
        lng = float(response_json["nearest"]["longt"])

        try:
            location = GoogleLocation.from_lat_lng(lat, lng, api_key)
        except GeolocationException as e:
            raise ValueError(str(e))

        location.country = location.country or country_name
        return location

    max_attempts = 20
    for _ in range(max_attempts):
        try:
            location = get_random_land_location()
        except ValueError:
            continue
        panos = search_panoramas(lat=location.lat, lon=location.lng)
        if panos:
            streetview = get_streetview(panos[0].pano_id, api_key=api_key)
            break
    else:
        return "Could not find a location with a street view image."

    image_url = upload_image(streetview)

    guess_games[chan] = GuessGame(
        location, datetime.now(pytz.timezone("UTC")), image_url
    )
    return f"🌎 Try to guess what country is: {image_url}"


@hook.command("geoguess", autohelp=False)
def geo_guess(text, chan, nick, reply, bot):
    """[country] - Play a game of GeoGuessr with a random location. Use 'reveal' to show the answer."""
    global guess_games
    text = text.strip()
    if text == "reveal":
        if chan not in guess_games:
            return "There is no active GeoGuess game in this channel. Start one with '.geoguess'."
        location = guess_games[chan].location
        del guess_games[chan]
        return f"🌎 The location was: {location} - Try again!"

    if text:
        if chan not in guess_games:
            return "There is no active GeoGuess game in this channel. Start one with '.geoguess'."

        location = guess_games[chan].location
        if fuzz.ratio(text.casefold(), location.country.casefold()) > 80:
            now = datetime.now(pytz.timezone("UTC"))
            start_time = guess_games[chan].start_time
            del guess_games[chan]
            return f"🎉 {nick} guessed the country correctly! It was {location.location_name}. Game duration: {timeformat.time_since(start_time, now)}"
        else:
            return f"🔍 Incorrect guess. Try again!"

    if chan in guess_games and guess_games[chan].start_time + timedelta(
        seconds=MIN_GUESS_GAME_DURATION
    ) > datetime.now(pytz.timezone("UTC")):
        return f"There is already an active GeoGuess game in this channel {guess_games[chan].image_url} - Try to guess the country with '.geoguess <country>'."

    reply("🔍 Starting a new GeoGuess game...")
    return new_guess_game(bot, chan)
