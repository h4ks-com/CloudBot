from datetime import datetime

import requests

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session


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
        params: dict[str, str] = {"q": location, "appid": api_key, "units": "metric"}

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


@hook.command("forecast", "fc")
def forecast(text: str) -> list[str] | str:
    """<city> - Get weather forecast for <city>"""
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
            "cnt": 8,
        }

        response = get_session().get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        city = data["city"]["name"]
        country = data["city"]["country"]

        forecasts: list[str] = []
        for item in data["list"][:3]:
            dt = datetime.fromtimestamp(item["dt"])
            time_str = dt.strftime("%a %H:%M")
            temp = item["main"]["temp"]
            desc = item["weather"][0]["description"].title()
            forecasts.append(f"\x02{time_str}\x02: {temp}°C, {desc}")

        header = f"\x02{city}, {country}\x02 - Forecast:"
        return [header] + forecasts

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
