"""
Fish minigame plugin for CloudBot.
Users can catch different types of fish with varying rarities.
"""

import random
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    select,
)

from cloudbot import hook
from cloudbot.util import database
from cloudbot.util.colors import get_format, parse
from cloudbot.util.formatting import pluralize_auto

# Configuration constants
BAITS_PER_RESET = 3
BAIT_RESET_HOURS = 2
BAIT_RESET_SECONDS = BAIT_RESET_HOURS * 3600
BAIT_WASTE_CHANCE = 0.60  # 60% chance bait is wasted without any hook


@dataclass
class Fish:
    name: str
    rarity: float  # Probability out of 1.0
    ascii_art: str


# Fish configuration - sorted by rarity (rarest first)
FISH_TYPES = [
    Fish(
        name="Whale",
        rarity=0.01,
        ascii_art=parse(
            """$(white,blue)                              $(clear)
$(white,blue)       $(black,blue).                      $(clear)
$(white,blue)      $(black,blue)":"                      $(clear)
$(white,blue)  $(black,blue)___:____     |"\\/"|         $(clear)
$(white,blue) $(black,blue),'        `.    \\  /         $(clear)
$(white,blue) $(black,blue)|  $(white,blue)O$(black,blue)        \\___/  |         $(clear)
$(cyan,blue)~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^$(clear)"""
        ),
    ),
    Fish(
        name="Seahorse",
        rarity=0.03,
        ascii_art=parse(
            """$(white,blue)      $(orange,blue)\\/)/)                  $(clear)
$(white,blue)     $(orange,blue)_'  oo(_.-.              $(clear)
$(white,blue)   $(orange,blue)/'.     .---'              $(clear)
$(white,blue) $(orange,blue)/'-./    (                   $(clear)
$(white,blue) $(orange,blue))     ; __\\                  $(clear)
$(white,blue) $(orange,blue)\\_.'\\  : __|                 $(clear)
$(white,blue)     $(orange,blue))  _/                    $(clear)
$(white,blue)    $(orange,blue)(  (,.                    $(clear)
$(white,blue)  $(orange,blue)mrf'-.-'                    $(clear)"""
        ),
    ),
    Fish(
        name="Tuna",
        rarity=0.08,
        ascii_art=parse(
            """$(white,blue)      $(gray,blue)/`·.¸                 $(clear)
$(white,blue)      $(gray,blue)/¸...¸`:·              $(clear)
$(white,blue)  $(gray,blue)¸.·´  ¸   `·.¸.·´)         $(clear)
$(white,blue) $(gray,blue): © ):´;      ¸  {          $(clear)
$(white,blue)  $(gray,blue)`·.¸ `·  ¸.·´\\`·¸)         $(clear)
$(white,blue)      $(gray,blue)`\\\\´´\\¸.·´             $(clear)"""
        ),
    ),
    Fish(
        name="Jellyfish",
        rarity=0.12,
        ascii_art=parse(
            """$(white,blue)      $(pink,blue)_______                $(clear)
$(white,blue)  $(pink,blue),-~~~       ~~~-,           $(clear)
$(white,blue) $(pink,blue)(                 )          $(clear)
$(white,blue)  $(pink,blue)\\_-, , , , , ,-_/           $(clear)
$(white,blue)     $(pink,blue)/ / | | \\ \\              $(clear)
$(white,blue)     $(pink,blue)| | | | | |              $(clear)
$(white,blue)     $(pink,blue)| | | | | |              $(clear)
$(white,blue)    $(pink,blue)/ / /   \\ \\ \\             $(clear)
$(white,blue)    $(pink,blue)| | |   | | |             $(clear)"""
        ),
    ),
    Fish(
        name="Baby Shark",
        rarity=0.15,
        ascii_art=parse(
            """$(white,blue)        $(dgray,blue).                     $(clear)
$(white,blue) $(dgray,blue)\\_____)\\_____                 $(clear)
$(white,blue) $(dgray,blue)/--v____ __`<                 $(clear)
$(white,blue)         $(dgray,blue))/                    $(clear)
$(white,blue)         $(dgray,blue)'                     $(clear)"""
        ),
    ),
    Fish(
        name="Silver Carp",
        rarity=0.20,
        ascii_art=parse(
            """$(white,blue)      $(gray,blue)/"*._         _        $(clear)
$(white,blue)   $(gray,blue).-*'`    `*-.._.-'/        $(clear)
$(white,blue) $(gray,blue)< * ))     ,       (         $(clear)
$(white,blue)   $(gray,blue)`*-._`._(__.--*"`.         $(clear)"""
        ),
    ),
    Fish(
        name="Cory",
        rarity=0.25,
        ascii_art=parse(
            """$(white,blue)        $(brown,blue)/\\                   $(clear)
$(white,blue)       $(brown,blue)_/./                   $(clear)
$(white,blue)  $(brown,blue),-'    `-:..-'/              $(clear)
$(white,blue) $(brown,blue): o )      _  (               $(clear)
$(white,blue) $(brown,blue)"`-....,--; `-=\\              $(clear)
$(white,blue)       $(brown,blue)`'                     $(clear)"""
        ),
    ),
    Fish(
        name="Carp",
        rarity=0.35,
        ascii_art=parse(
            """$(white,blue)  $(orange,blue);,//;,    ,;/               $(clear)
$(white,blue)  $(orange,blue)o:::::::;;///               $(clear)
$(white,blue) $(orange,blue)>::::::::;;\\\\\\               $(clear)
$(white,blue)   $(orange,blue)''\\\\\\\\'" ';\\               $(clear)"""
        ),
    ),
    Fish(
        name="Fry",
        rarity=0.70,
        ascii_art=parse(
            """$(white,blue)  $(yellow,blue)_                           $(clear)
$(white,blue) $(yellow,blue)><_>                          $(clear)"""
        ),
    ),
]

# Special no-catch result
FISH_RESTS = Fish(
    name="Nothing",
    rarity=0.0,  # Calculated as remainder
    ascii_art=parse(
        """$(white,blue) $(brown,blue)|\\    \\ \\ \\ \\ \\ \\ \\      __    $(clear)
$(white,blue) $(brown,blue)|  \\    \\ \\ \\ \\ \\ \\ \\   | O~-_ $(clear)
$(white,blue) $(brown,blue)|   >----|-|-|-|-|-|-|--|  __/ $(clear)
$(white,blue) $(brown,blue)|  /    / / / / / / /   |__\\   $(clear)
$(white,blue) $(brown,blue)|/     / / / / / / /           $(clear)"""
    ),
)

# Database tables
fish_catches_table = Table(
    "fish_catches",
    database.metadata,
    Column("username", String),
    Column("fish_type", String),
    Column("count", Integer, default=0),
    PrimaryKeyConstraint("username", "fish_type"),
)

fish_baits_table = Table(
    "fish_baits",
    database.metadata,
    Column("username", String, primary_key=True),
    Column("baits", Integer, default=BAITS_PER_RESET),
    Column("last_reset", Integer),  # Unix timestamp
)


def bold(text: str) -> str:
    """Make text bold for IRC."""
    return f"{get_format('bold')}{text}{get_format('clear')}"


def italic(text: str) -> str:
    """Make text italic for IRC."""
    return f"{get_format('italic')}{text}{get_format('clear')}"


def get_bait_status(username: str, db: Any) -> tuple[int, bool]:
    """Get current bait count and whether baits were reset."""
    username = username.lower()
    current_time = int(time.time())

    result = db.execute(
        select([fish_baits_table.c.baits, fish_baits_table.c.last_reset]).where(
            fish_baits_table.c.username == username
        )
    ).fetchone()

    if not result:
        # New user - add them with full baits
        db.execute(
            fish_baits_table.insert().values(
                username=username,
                baits=BAITS_PER_RESET,
                last_reset=current_time,
            )
        )
        db.commit()
        return BAITS_PER_RESET, False

    baits, last_reset = result
    time_since_reset = current_time - last_reset

    if time_since_reset >= BAIT_RESET_SECONDS:
        # Reset baits
        db.execute(
            fish_baits_table.update()
            .where(fish_baits_table.c.username == username)
            .values(baits=BAITS_PER_RESET, last_reset=current_time)
        )
        db.commit()
        return BAITS_PER_RESET, True

    return baits, False


def use_bait(username: str, db: Any) -> bool:
    """Use one bait if available. Returns True if successful."""
    username = username.lower()

    result = db.execute(
        select([fish_baits_table.c.baits]).where(
            fish_baits_table.c.username == username
        )
    ).fetchone()

    if not result or result[0] <= 0:
        return False

    db.execute(
        fish_baits_table.update()
        .where(fish_baits_table.c.username == username)
        .values(baits=result[0] - 1)
    )
    db.commit()
    return True


def get_time_until_reset(username: str, db: Any) -> int:
    """Get seconds until bait reset."""
    username = username.lower()
    current_time = int(time.time())

    result = db.execute(
        select([fish_baits_table.c.last_reset]).where(
            fish_baits_table.c.username == username
        )
    ).fetchone()

    if not result:
        return 0

    time_since_reset = current_time - result[0]
    return max(0, BAIT_RESET_SECONDS - time_since_reset)


def catch_fish() -> Fish | None:
    """Determine what fish (if any) was caught. Returns None if bait was wasted."""
    # First stage: Check if bait is completely wasted (no hook at all)
    if random.random() < BAIT_WASTE_CHANCE:
        return None

    # Second stage: Something hooked! This includes both fish AND the "Nothing" result
    # The "Nothing" result represents something that hooked but got away

    # Calculate total probability including the "Nothing" result
    total_hook_prob = (
        sum(fish.rarity for fish in FISH_TYPES) + 0.3
    )  # Add Nothing probability

    roll = random.random() * total_hook_prob

    # Check for "Nothing" result first (something hooked but got away)
    if roll < 0.3:
        return FISH_RESTS

    # Adjust roll for actual fish selection
    adjusted_roll = roll - 0.3

    # Determine which fish was caught
    cumulative_prob = 0.0
    for fish in FISH_TYPES:
        cumulative_prob += fish.rarity
        if adjusted_roll <= cumulative_prob:
            return fish

    # Fallback to rarest fish if we somehow get here
    return FISH_TYPES[0]


def record_catch(username: str, fish: Fish, db: Any) -> int:
    """Record a fish catch and return new total count."""
    if fish.name == "Nothing":
        return 0

    username = username.lower()

    result = db.execute(
        select([fish_catches_table.c.count])
        .where(fish_catches_table.c.username == username)
        .where(fish_catches_table.c.fish_type == fish.name)
    ).fetchone()

    if result:
        new_count = result[0] + 1
        db.execute(
            fish_catches_table.update()
            .where(fish_catches_table.c.username == username)
            .where(fish_catches_table.c.fish_type == fish.name)
            .values(count=new_count)
        )
    else:
        new_count = 1
        db.execute(
            fish_catches_table.insert().values(
                username=username, fish_type=fish.name, count=new_count
            )
        )

    db.commit()
    return new_count


@hook.command("fish", autohelp=False)
def fish_command(nick: str, db: Any) -> str | list[str]:
    """- Cast your line and try to catch a fish!"""
    baits, was_reset = get_bait_status(nick, db)

    if baits <= 0:
        time_left = get_time_until_reset(nick, db)
        hours = time_left // 3600
        minutes = (time_left % 3600) // 60

        if hours > 0:
            time_str = f"{hours}h {minutes}m"
        else:
            time_str = f"{minutes}m"

        return f"🎣 You're out of bait! Try again in {bold(time_str)}"

    if not use_bait(nick, db):
        return "🎣 You're out of bait! Try again later."

    caught_fish = catch_fish()

    response = [f"🎣 {bold(nick)} casts their line..."]

    if caught_fish is None:
        # Bait was wasted - no fish selection occurred
        response.extend(
            [
                "Your bait drifts away unused... 🌊💔",
                f"Bait remaining: {bold(str(baits - 1))}",
            ]
        )
    elif caught_fish.name == "Nothing":
        response.append("Nothing bites this time! 🌊")
        response.extend(caught_fish.ascii_art.split("\n"))
        response.append(f"Bait remaining: {bold(str(baits - 1))}")
    else:
        count = record_catch(nick, caught_fish, db)
        rarity_desc = (
            "legendary"
            if caught_fish.rarity <= 0.05
            else "rare" if caught_fish.rarity <= 0.15 else "common"
        )

        response.append(
            f"🎉 You caught a {italic(rarity_desc)} {bold(caught_fish.name)}! (#{count})"
        )
        response.extend(caught_fish.ascii_art.split("\n"))
        response.append(f"Bait remaining: {bold(str(baits - 1))}")

    return response


@hook.command("fishes", autohelp=False)
def fishes_command(text: str, nick: str, db: Any) -> str:
    """[username] - Show fish collection for yourself or another user"""
    if text and text.strip():
        target = text.strip().lower()
        display_name = text.strip()
    else:
        target = nick.lower()
        display_name = nick

    results = db.execute(
        select(
            [fish_catches_table.c.fish_type, fish_catches_table.c.count]
        ).where(fish_catches_table.c.username == target)
    ).fetchall()

    if not results:
        return f"🐟 {bold(display_name)} hasn't caught any fish yet!"

    total_fish = sum(count for _, count in results)
    fish_summary = ", ".join(
        f"{fish_type}: {count}" for fish_type, count in sorted(results)
    )

    return f"🐟 {bold(display_name)}'s collection ({bold(str(total_fish))} total): {fish_summary}"


@hook.command("fishstats", autohelp=False)
def fishstats_command(db: Any) -> str:
    """- Show top fishers across all channels"""
    # Get total catches per user
    results = db.execute(
        select([fish_catches_table.c.username, fish_catches_table.c.count])
    ).fetchall()

    if not results:
        return "🐟 No fish have been caught yet!"

    # Sum up total catches per user
    user_totals: dict[str, int] = {}
    for username, count in results:
        user_totals[username] = user_totals.get(username, 0) + count

    # Get top 5 fishers
    top_fishers = sorted(user_totals.items(), key=lambda x: x[1], reverse=True)[
        :5
    ]

    if not top_fishers:
        return "🐟 No fish have been caught yet!"

    rankings = []
    for i, (username, total) in enumerate(top_fishers, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🐟"
        rankings.append(
            f"{medal} {bold(username)}: {pluralize_auto(total, 'fish')}"
        )

    return f"🎣 Top Fishers: {' • '.join(rankings)}"


@hook.command("baits", autohelp=False)
def baits_command(nick: str, db: Any) -> str:
    """- Show your remaining bait count and time until reset"""
    baits, was_reset = get_bait_status(nick, db)

    if baits <= 0:
        time_left = get_time_until_reset(nick, db)
        hours = time_left // 3600
        minutes = (time_left % 3600) // 60

        if hours > 0:
            time_str = f"{hours}h {minutes}m"
        else:
            time_str = f"{minutes}m"

        return f"🎣 You have {bold('0')} baits remaining. Reset in {bold(time_str)}"
    else:
        return f"🎣 You have {bold(str(baits))} {'bait' if baits == 1 else 'baits'} remaining"


@hook.command("fishreset", permissions=["admin", "botcontrol"])
def fishreset_command(text: str, db: Any) -> str:
    """<username> - Reset a user's bait count and timestamp (Admin only)"""
    if not text or not text.strip():
        return "🎣 Usage: .fishreset <username>"

    target_user = text.strip().lower()
    current_time = int(time.time())

    # Check if user exists in bait table
    result = db.execute(
        select([fish_baits_table.c.username]).where(
            fish_baits_table.c.username == target_user
        )
    ).fetchone()

    if result:
        # Update existing user
        db.execute(
            fish_baits_table.update()
            .where(fish_baits_table.c.username == target_user)
            .values(baits=BAITS_PER_RESET, last_reset=current_time)
        )
        db.commit()
        return f"🎣 Reset {bold(text.strip())}'s bait count to {BAITS_PER_RESET} baits"
    else:
        # Create new user entry
        db.execute(
            fish_baits_table.insert().values(
                username=target_user,
                baits=BAITS_PER_RESET,
                last_reset=current_time,
            )
        )
        db.commit()
        return f"🎣 Created new bait entry for {bold(text.strip())} with {BAITS_PER_RESET} baits"
