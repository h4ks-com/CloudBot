# Get range of pi digits

from collections.abc import Iterator

from cloudbot import hook
from cloudbot.util.web import get_session

API = "https://api.pi.delivery/v1/pi"
MAX_DIGITS = 400


def pi_range(start: int, size: int) -> Iterator[str]:
    response = get_session().get(
        API, params={"start": start, "numberOfDigits": size, "radix": 10}
    )
    if response.status_code == 200:
        yield response.json().get("content")


@hook.command("pi", autohelp=False)
def pi(text: str):
    """<start> <size> - Gets the first <size> digits of pi starting at <start>"""
    start_s: str
    size_s: str
    try:
        start_s, size_s = text.split()
    except ValueError:
        size_s = str(MAX_DIGITS)
        if text:
            start_s = text
        else:
            start_s = "0"
    try:
        start = int(start_s)
        size = int(size_s)
    except ValueError:
        return "Usage: .pi <start> <size>"

    if size > MAX_DIGITS:
        return f"Size must be less than {MAX_DIGITS}"
    if size < 0:
        return "Size must be greater than 0"

    if start < 0:
        return "Start must be greater than 0"

    return "".join(pi_range(start, size))
