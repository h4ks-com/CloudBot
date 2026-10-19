"""
gaming.py

Dice, coins, and random generation for gaming.

Modified By:
    - Luke Rogers <https://github.com/lukeroge>

License:
    GPL v3
"""

import random
import re
from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg

whitespace_re = re.compile(r"\s+")
valid_diceroll = re.compile(
    r"^([+-]?(?:\d+|\d*d(?:\d+|F))(?:[+-](?:\d+|\d*d(?:\d+|F)))*)( .+)?$", re.I
)
sign_re = re.compile(r"[+-]?(?:\d*d)?(?:\d+|F)", re.I)
split_re = re.compile(r"([\d+-]*)d?(F|\d*)", re.I)


def clamp(n, min_value, max_value):
    """Restricts a number to a certain range of values,
    returning the min or max value if the value is too small or large, respectively
    :param n: The value to clamp
    :param min_value: The minimum possible value
    :param max_value: The maximum possible value
    :return: The clamped value
    """
    return min(max(n, min_value), max_value)


def n_rolls(count, n):
    """roll an n-sided die count times"""
    if n in ("f", "F"):
        return [random.randint(-1, 1) for _ in range(min(count, 100))]

    if count < 100:
        return [random.randint(1, n) for _ in range(count)]

    normalvariate = approximate(count, n)

    return [int(normalvariate)]


def approximate(count, n):
    # Calculate a random sum approximated using a randomized normal variate with the midpoint used as the mu
    # and an approximated standard deviation based on variance as the sigma
    mid = 0.5 * (n + 1) * count
    var = (n**2 - 1) / 12
    adj_var = (var * count) ** 0.5
    normalvariate = random.normalvariate(mid, adj_var)
    return normalvariate


@hook.command("roll", "dice")
def dice(
    event: CommandEvent,
    dice_roll: Annotated[str, Arg("Dice roll, like 2d20-d5+4")],
):
    """simulates dice rolls. Example: 'dice 2d20-d5+4 roll 2': D20s, subtract 1D5, add 4"""
    match = valid_diceroll.match(dice_roll)
    if not match:
        event.notice(f"Invalid dice roll '{dice_roll}'")
        return None

    text, desc = match.groups()

    if "d" not in text:
        return None

    spec = whitespace_re.sub("", text)
    groups = sign_re.findall(spec)

    total = 0
    rolls = []

    for roll in groups:
        _count, _side = split_re.match(roll).groups()
        count = int(_count) if _count not in " +-" else 1
        if _side.upper() == "F":  # fudge dice are basically 1d3-2
            for fudge in n_rolls(count, "F"):
                if fudge == 1:
                    rolls.append("\x033+\x0f")
                elif fudge == -1:
                    rolls.append("\x034-\x0f")
                else:
                    rolls.append("0")
                total += fudge
        elif _side == "":
            total += count
        else:
            side = int(_side)
            try:
                if count > 0:
                    d = n_rolls(count, side)
                    rolls += list(map(str, d))
                    total += sum(d)
                else:
                    d = n_rolls(-count, side)
                    rolls += [str(-x) for x in d]
                    total -= sum(d)
            except OverflowError:
                # I have never seen this happen. If you make this happen, you win a cookie
                event.reply("Thanks for overflowing a float, jerk >:[")
                raise

    if desc:
        return "{}: {} ({})".format(desc.strip(), total, ", ".join(rolls))

    return "{} ({})".format(total, ", ".join(rolls))


@hook.command()
def choose(
    event: CommandEvent,
    choices_text: Annotated[
        str, Arg("Choices separated by commas, or by 'or'")
    ],
):
    """randomly picks one of the given choices"""
    choices = re.findall(r"([^,]+)", choices_text.strip())
    if len(choices) == 1:
        choices = choices[0].split(" or ")
        if len(choices) == 1:
            event.notice_doc()
            return None

    return random.choice([choice.strip() for choice in choices])


@hook.command(autohelp=False)
def coin(
    event: CommandEvent, amount: Annotated[int, Arg("How many coins")] = 1
):
    """flips [amount] coins"""
    if amount == 1:
        event.action(
            "flips a coin and gets {}.".format(
                random.choice(["heads", "tails"])
            )
        )
        return None

    if amount == 0:
        event.action("makes a coin flipping motion")
        return None

    mu = 0.5 * amount
    sigma = (0.75 * amount) ** 0.5
    n = random.normalvariate(mu, sigma)
    heads = clamp(int(round(n)), 0, amount)
    tails = amount - heads
    event.action(
        f"flips {amount} coins and gets {heads} heads and {tails} tails."
    )
    return None
