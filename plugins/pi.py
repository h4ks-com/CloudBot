# Get range of pi digits

from collections.abc import Iterator
from typing import Annotated

from cloudbot import hook
from cloudbot.hook import Arg
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
def pi(
    start: Annotated[int, Arg("First digit to show")] = 0,
    size: Annotated[int, Arg("How many digits, at most 400")] = MAX_DIGITS,
):
    """Gets the first <size> digits of pi starting at <start>"""
    if size > MAX_DIGITS:
        return f"Size must be less than {MAX_DIGITS}"
    if size < 0:
        return "Size must be greater than 0"

    if start < 0:
        return "Start must be greater than 0"

    return "".join(pi_range(start, size))
