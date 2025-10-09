import random

import requests

from cloudbot import hook
from cloudbot.util.web import get_session


@hook.command("forecast", "fc")
def forecast(text):
    """<query> - returns the weather forecast result for <query>"""
    response = get_session().get(
        f"http://wttr.in/{'+'.join(text.split())}?format=j1"
    )
    if response.status_code == 200:
        try:
            j = response.json()
        except Exception as e:
            return f"Error: {e}" + " -- " + response.text
        nearest = j["nearest_area"][0]
        area = nearest["areaName"][0]["value"]
        message = [
            f"{nearest['country'][0]['value']} - {nearest['region'][0]['value']} - {area}, lat: {nearest['latitude']}  long: {nearest['longitude']}"
        ]
        for day in j["weather"]:
            message.append(
                f"\x02{day['date']}\x02 - \x02average\x02: {day['avgtempC']}ºC, \x02max\x02: {day['maxtempC']}ºC, \x02min\x02: {day['mintempC']}ºC, \x02sun hours\x02: {day['sunHour']}, \x02precipitation\x02: {round(sum([float(h['precipMM']) for h in day['hourly']]), 3)}mm \x02"
            )
        return message
    else:
        return "City not found."


@hook.command("astronomy", "ast")
def astronomy(text):
    """<query> - returns the astronomy result for <query>"""
    response = get_session().get(
        f"http://wttr.in/{'+'.join(text.split())}?format=j1"
    )
    if response.status_code == 200:
        j = response.json()
        nearest = j["nearest_area"][0]
        message = [
            f"{nearest['country'][0]['value']} - {nearest['region'][0]['value']}, lat: {nearest['latitude']}  long: {nearest['longitude']}"
        ]
        for day in j["weather"]:
            ast = day["astronomy"][0]
            MOONS = {
                "First Quarter": "🌓",
                "Last Quarter": "🌗",
                "Third Quarter": "🌗",
                "Crescent Moon": "🌒",
                "Full Moon": "🌕",
                "New Moon": "🌑",
                "Crescent": "🌒",
                "Full": "🌕",
                "New": "🌑",
                "Waxing Gibbous": "🌖",
                "Waxing Crescent": "🌘",
                "Waning Gibbous": "🌔",
                "Waning Crescent": "🌘",
            }
            if ast["moon_phase"] in MOONS:
                moon = MOONS[ast["moon_phase"]] + " "
            message.append(
                f"\x02{day['date']}\x02 - {moon}"
                + ", ".join([f"\x02{key}\x02: {ast[key]}" for key in ast])
            )
        return message
    else:
        return "City not found."


@hook.command("time")
def time_command(text, reply):
    """<location> - Gets the current time in <location>."""
    formatted_time = (
        get_session().get(
            f"http://wttr.in/{'+'.join(text.split())}?format=\"%T %Z\"&nonce={random.randint(10**4, 10**6)}"
        )
        .text.strip()
        .split("-")[0]
        .replace("'", "")
        .replace('"', "")
    )
    j = get_session().get(
        f"http://wttr.in/{'+'.join(text.split())}?format=j1"
    ).json()
    location_name = f"{j['nearest_area'][0]['region'][0]['value']} - {j['nearest_area'][0]['country'][0]['value']}"
    return f"\x02{formatted_time}\x02 - {location_name}"
