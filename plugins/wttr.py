import math
from datetime import datetime

import pytz
import requests
from timezonefinder import TimezoneFinder

from cloudbot import hook
from cloudbot.util.web import get_session

tf = TimezoneFinder()


def get_moon_phase(date: datetime | None = None) -> tuple[str, str]:
    """Calculate moon phase and return (phase_name, emoji)."""
    if date is None:
        date = datetime.now()

    known_new_moon = datetime(2000, 1, 6, 18, 14)
    lunar_cycle = 29.530588853

    days_since = (date - known_new_moon).total_seconds() / 86400
    phase = (days_since % lunar_cycle) / lunar_cycle

    if phase < 0.0625 or phase >= 0.9375:
        return ("New Moon", "🌑")
    elif phase < 0.1875:
        return ("Waxing Crescent", "🌒")
    elif phase < 0.3125:
        return ("First Quarter", "🌓")
    elif phase < 0.4375:
        return ("Waxing Gibbous", "🌔")
    elif phase < 0.5625:
        return ("Full Moon", "🌕")
    elif phase < 0.6875:
        return ("Waning Gibbous", "🌖")
    elif phase < 0.8125:
        return ("Last Quarter", "🌗")
    else:
        return ("Waning Crescent", "🌘")


@hook.command("time", "tz")
def time_command(text: str) -> str:
    """<location> - Gets the current time in <location> using reliable geocoding."""
    if not text or not text.strip():
        return "Please provide a location. Usage: .time <location>"

    location = text.strip()

    url = "https://nominatim.openstreetmap.org/search"
    params: dict[str, str | int] = {"q": location, "format": "json", "limit": 1}
    headers: dict[str, str] = {
        "User-Agent": "CloudBot/IRC (https://github.com/TotallyNotRobots/CloudBot)"
    }

    try:
        response = get_session().get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()

        results = response.json()
        if not results:
            return f"Location '{location}' not found. Try being more specific."

        data = results[0]
        lat = float(data["lat"])
        lon = float(data["lon"])
        display_name = data["display_name"]

        tz_name = tf.timezone_at(lat=lat, lng=lon)

        if not tz_name:
            return f"Could not determine timezone for {location}"

        tz = pytz.timezone(tz_name)
        current_time = datetime.now(tz)

        time_str = current_time.strftime("%H:%M:%S %Z")

        parts = display_name.split(", ")
        if len(parts) >= 2:
            simple_name = ", ".join(parts[:2])
        else:
            simple_name = parts[0] if parts else display_name

        return f"\x02{time_str}\x02 - {simple_name}"

    except requests.exceptions.Timeout:
        return "Request timed out. Please try again."
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            return "Rate limited by geocoding service. Please try again in a moment."
        return f"Geocoding service error: HTTP {e.response.status_code}"
    except requests.exceptions.RequestException:
        return "Failed to connect to geocoding service. Please try again later."
    except (ValueError, KeyError, IndexError) as e:
        return f"Error parsing location data: {type(e).__name__}"


@hook.command("astronomy", "ast")
def astronomy(text: str) -> list[str] | str:
    """<location> - Get sunrise, sunset, and moon phase information for <location>."""
    if not text or not text.strip():
        return "Please provide a location. Usage: .ast <location>"

    location = text.strip()

    geo_url = "https://nominatim.openstreetmap.org/search"
    geo_params: dict[str, str | int] = {"q": location, "format": "json", "limit": 1}
    headers: dict[str, str] = {
        "User-Agent": "CloudBot/IRC (https://github.com/TotallyNotRobots/CloudBot)"
    }

    try:
        geo_response = get_session().get(
            geo_url, params=geo_params, headers=headers, timeout=10
        )
        geo_response.raise_for_status()

        results = geo_response.json()
        if not results:
            return f"Location '{location}' not found. Try being more specific."

        data = results[0]
        lat = float(data["lat"])
        lon = float(data["lon"])
        display_name = data["display_name"]

        parts = display_name.split(", ")
        simple_name = ", ".join(parts[:2]) if len(parts) >= 2 else parts[0]

        astro_url = "https://api.sunrise-sunset.org/json"
        astro_params: dict[str, float | int] = {"lat": lat, "lng": lon, "formatted": 0}

        astro_response = get_session().get(astro_url, params=astro_params, timeout=10)
        astro_response.raise_for_status()

        astro_data = astro_response.json()

        if astro_data["status"] != "OK":
            return "Failed to get astronomy data for this location."

        results = astro_data["results"]

        sunrise_utc = datetime.fromisoformat(results["sunrise"].replace("Z", "+00:00"))
        sunset_utc = datetime.fromisoformat(results["sunset"].replace("Z", "+00:00"))
        solar_noon_utc = datetime.fromisoformat(
            results["solar_noon"].replace("Z", "+00:00")
        )

        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            tz = pytz.timezone(tz_name)
            sunrise_local = sunrise_utc.astimezone(tz)
            sunset_local = sunset_utc.astimezone(tz)
            solar_noon_local = solar_noon_utc.astimezone(tz)
        else:
            sunrise_local = sunrise_utc
            sunset_local = sunset_utc
            solar_noon_local = solar_noon_utc

        day_length_seconds = results["day_length"]
        hours = day_length_seconds // 3600
        minutes = (day_length_seconds % 3600) // 60

        phase_name, phase_emoji = get_moon_phase()

        return [
            f"\x02{simple_name}\x02 - Astronomy Info:",
            f"\x02Sunrise\x02: {sunrise_local.strftime('%H:%M:%S %Z')}",
            f"\x02Sunset\x02: {sunset_local.strftime('%H:%M:%S %Z')}",
            f"\x02Solar Noon\x02: {solar_noon_local.strftime('%H:%M:%S %Z')}",
            f"\x02Day Length\x02: {hours}h {minutes}m",
            f"\x02Moon Phase\x02: {phase_emoji} {phase_name}",
        ]

    except requests.exceptions.Timeout:
        return "Request timed out. Please try again."
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            return "Rate limited by service. Please try again in a moment."
        return f"Service error: HTTP {e.response.status_code}"
    except requests.exceptions.RequestException:
        return "Failed to connect to service. Please try again later."
    except (ValueError, KeyError, IndexError) as e:
        return f"Error parsing data: {type(e).__name__}"
