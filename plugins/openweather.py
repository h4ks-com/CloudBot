from collections import defaultdict
from datetime import datetime

import requests

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session


def weather_emoji(weather_id: int, icon: str) -> str:
    """Map OWM weather ID and icon code to a representative emoji.

    Night icons end with 'n'; day icons end with 'd'.
    """
    is_night = icon.endswith("n")
    if weather_id < 300:
        return "⛈"
    elif weather_id < 400:
        return "🌦"
    elif weather_id < 600:
        return "🌧"
    elif weather_id < 700:
        return "❄️"
    elif weather_id < 800:
        return "🌫"
    elif weather_id == 800:
        return "🌙" if is_night else "☀️"
    elif weather_id == 801:
        return "🌤"
    elif weather_id == 802:
        return "⛅"
    else:
        return "☁️"


@hook.command("we", "weather")
def weather(text: str) -> str:
    """<city> - Get the current weather of <city>"""
    api_key = bot.config.get_api_key("openwheater")
    if not api_key:
        return "This command requires an OpenWeatherMap API key. Get one free at https://openweathermap.org/api"

    if not text or not text.strip():
        return "Please provide a city name"

    location = text.strip()

    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params: dict[str, str] = {
            "q": location,
            "appid": api_key,
            "units": "metric",
        }

        response = get_session().get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        city = data["name"]
        country = data["sys"]["country"]
        temp = data["main"]["temp"]
        temp_min = data["main"]["temp_min"]
        temp_max = data["main"]["temp_max"]
        feels_like = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        description = data["weather"][0]["description"].title()
        wind_speed = data["wind"]["speed"]

        return (
            f"\x02{city}, {country}\x02: {temp}°C (feels like {feels_like}°C), "
            f"{description}, \x02Min\x02: {temp_min}°C, \x02Max\x02: {temp_max}°C, "
            f"\x02Humidity\x02: {humidity}%, \x02Wind\x02: {wind_speed} m/s"
        )

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f"Location '{location}' not found. Try being more specific."
        if e.response.status_code == 401:
            return "Invalid API key configured."
        return f"Weather API error: HTTP {e.response.status_code}"
    except requests.exceptions.Timeout:
        return "Request timed out. Please try again."
    except requests.exceptions.RequestException:
        return "Failed to connect to weather service. Please try again later."
    except (KeyError, IndexError, ValueError) as e:
        return f"Error parsing weather data: {type(e).__name__}"


@hook.command("forecast", "fc", "fcd")
def forecast(text: str) -> list[str] | str:
    """<city> - Get today's hourly forecast for <city> (every 3h, ~27h ahead)"""
    api_key = bot.config.get_api_key("openwheater")
    if not api_key:
        return "This command requires an OpenWeatherMap API key. Get one free at https://openweathermap.org/api"

    if not text or not text.strip():
        return "Please provide a city name"

    location = text.strip()

    try:
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params: dict[str, str | int] = {
            "q": location,
            "appid": api_key,
            "units": "metric",
            "cnt": 9,  # 9 × 3h = 27 hours ahead, displayed in 3 lines of 3
        }

        response = get_session().get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        city = data["city"]["name"]
        country = data["city"]["country"]

        slots = []
        for item in data["list"]:
            dt = datetime.fromtimestamp(item["dt"])
            emoji = weather_emoji(
                item["weather"][0]["id"], item["weather"][0]["icon"]
            )
            temp = round(item["main"]["temp"])
            slots.append(f"{emoji} {dt.strftime('%H:%M')} {temp}°C")

        header = f"\x02{city}, {country}\x02 🕐 Forecast:"
        lines = [" │ ".join(slots[i : i + 3]) for i in range(0, len(slots), 3)]
        return [header] + lines

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f"Location '{location}' not found. Try being more specific."
        if e.response.status_code == 401:
            return "Invalid API key configured."
        return f"Weather API error: HTTP {e.response.status_code}"
    except requests.exceptions.Timeout:
        return "Request timed out. Please try again."
    except requests.exceptions.RequestException:
        return "Failed to connect to weather service. Please try again later."
    except (KeyError, IndexError, ValueError) as e:
        return f"Error parsing forecast data: {type(e).__name__}"


@hook.command("forecastweek", "fcw")
def forecast_week(text: str) -> list[str] | str:
    """<city> - Get a multi-day weather forecast for <city>"""
    api_key = bot.config.get_api_key("openwheater")
    if not api_key:
        return "This command requires an OpenWeatherMap API key. Get one free at https://openweathermap.org/api"

    if not text or not text.strip():
        return "Please provide a city name"

    location = text.strip()

    try:
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params: dict[str, str | int] = {
            "q": location,
            "appid": api_key,
            "units": "metric",
            "cnt": 40,  # up to 5 days of 3h intervals
        }

        response = get_session().get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        city = data["city"]["name"]
        country = data["city"]["country"]

        by_day: dict = defaultdict(list)
        for item in data["list"]:
            day = datetime.fromtimestamp(item["dt"]).date()
            by_day[day].append(item)

        day_summaries = []
        for day, items in sorted(by_day.items()):
            temps = [item["main"]["temp"] for item in items]
            # Pick noon-ish slot for the representative condition/emoji
            midday = min(
                items,
                key=lambda x: abs(datetime.fromtimestamp(x["dt"]).hour - 12),
            )
            emoji = weather_emoji(
                midday["weather"][0]["id"], midday["weather"][0]["icon"]
            )
            high = round(max(temps))
            low = round(min(temps))
            day_summaries.append(
                f"\x02{day.strftime('%a')}\x02 {emoji} {high}/{low}°C"
            )

        header = f"\x02{city}, {country}\x02 📅 {len(day_summaries[:5])}-Day:"
        return [header, " │ ".join(day_summaries[:5])]

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f"Location '{location}' not found. Try being more specific."
        if e.response.status_code == 401:
            return "Invalid API key configured."
        return f"Weather API error: HTTP {e.response.status_code}"
    except requests.exceptions.Timeout:
        return "Request timed out. Please try again."
    except requests.exceptions.RequestException:
        return "Failed to connect to weather service. Please try again later."
    except (KeyError, IndexError, ValueError) as e:
        return f"Error parsing forecast data: {type(e).__name__}"
