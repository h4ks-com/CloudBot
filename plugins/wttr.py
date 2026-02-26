from datetime import datetime

import pytz
import requests
from timezonefinder import TimezoneFinder

from cloudbot import hook
from cloudbot.util.web import get_session

tf = TimezoneFinder()


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
