import requests

from cloudbot import hook


@hook.command("chess", "lichess", autohelp=False)
def lichess(text: str) -> list[str] | str:
    """[time-limit] - Create a random lichess game link to play with a friend, with optional time limit in minutes"""
    text = text.strip()
    data = {}
    if text:
        if not text.isdigit():
            return "Usage: .chess [time-limit]"
        time_limit = int(text) * 60
        if time_limit < 180:
            return "Time limit must be greater than 3 minutes"
        data["clock.limit"] = time_limit
        data["clock.increment"] = 1

    try:
        response = requests.post(
            "https://lichess.org/api/challenge/open",
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except requests.HTTPError as e:
        return f"Error: {e}"

    body = response.json()
    if "error" in body:
        return body["error"]
    return [body.get("url"), body.get("urlWhite"), body.get("urlBlack")]
