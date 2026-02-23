import requests

from cloudbot import hook
from cloudbot.bot import bot

api_key = bot.config.get_api_key("openwheater")

if not api_key:
    raise Exception("Error: missing api key for openweather")


@hook.command("we", "weather")
def weater(text):
    """<city> - Get the current weather of <city>"""
    if not text:
        return "Please provide a city name"

    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": text,
            "appid": api_key,
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "message" in data:
            return data["message"]

        name = data["name"]
        country = data["sys"]["country"]
        lat = data["coord"]["lat"]
        lon = data["coord"]["lon"]
        description = data["weather"][0]["description"]
        temp = round(data["main"]["temp"] - 273.15)
        temp_min = round(data["main"]["temp_min"] - 273.15)
        temp_max = round(data["main"]["temp_max"] - 273.15)
        feels_like = round(data["main"]["feels_like"] - 273.15)
        humidity = data["main"]["humidity"]

        return (
            f"{name} (\x02Country\x02: {country}, \x02lat\x02: {lat}, \x02long\x02: {lon}) --"
            f" \x02{description}\x02 {temp}Cº \x02min\x02:"
            f" {temp_min}Cº \x02max\x02: {temp_max}Cº \x02sensation\x02:"
            f" {feels_like}Cº \x02humidity\x02: {humidity}%"
        )
    except requests.exceptions.RequestException as e:
        return f"Error fetching weather data: {e}"
    except (KeyError, IndexError) as e:
        return f"Error parsing weather data: {e}"
