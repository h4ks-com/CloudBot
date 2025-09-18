"""
battery.py - A silly plugin about licking batteries
"""

import random

from cloudbot import hook

# Battery types with increasingly silly voltages
BATTERY_TYPES = [
    "9V",
    "12V",
    "AA 1.5V",
    "AAA 1.5V",
    "D-cell 1.5V",
    "car battery 12V",
    "lithium-ion 3.7V",
    "CR2032 3V",
    "laptop battery 19V",
    "electric fence 10,000V",
    "power line 120V",
    "industrial 480V",
    "Tesla coil 100,000V",
    "lightning bolt 1,000,000,000V",
    "nuclear reactor control rod",
    "alien power cell ∞V",
    "interdimensional battery ?V",
    "quantum battery ±∞V",
    "potato battery 0.9V",
    "lemon battery 0.8V",
]

# Silly adjectives for the licking experience
LICK_ADJECTIVES = [
    "enthusiastically",
    "cautiously",
    "bravely",
    "foolishly",
    "scientifically",
    "professionally",
    "experimentally",
    "curiously",
    "recklessly",
    "methodically",
    "passionately",
    "nervously",
    "confidently",
    "mysteriously",
    "aggressively",
    "delicately",
    "expertly",
    "accidentally",
    "intentionally",
    "vigorously",
]

# Silly results/effects
LICK_EFFECTS = [
    "and their hair stands on end",
    "and sees colors that don't exist",
    "and briefly achieves enlightenment",
    "and can taste electrons",
    "and their tongue goes numb",
    "and sparks fly",
    "and gains temporary superpowers",
    "and questions their life choices",
    "and transcends space-time",
    "and can now speak to electrical appliances",
    "and discovers the meaning of life",
    "and their tongue tingles magnificently",
    "and becomes magnetically attractive",
    "and glows faintly in the dark",
    "and can now charge phones with their tongue",
    "and hears the universe humming",
    "and briefly becomes a human conductor",
    "and tastes the rainbow",
    "and unlocks a new flavor: electric blue",
    "and their taste buds do a little dance",
]


def get_random_number() -> str:
    """Generate an absurdly high random number for comedic effect."""
    # Random number between 1 million and 999 billion
    base = random.randint(1_000_000, 999_999_999_999)

    # Sometimes add ridiculous precision
    if random.random() < 0.3:
        decimal = random.randint(1, 999)
        return f"{base:,}.{decimal}"

    return f"{base:,}"


@hook.command("lick", autohelp=False)
def battery_lick(text: str, nick: str, chan: str, message) -> str:
    """- Lick a battery for science! (Warning: Do not actually lick batteries)"""

    # Small chance the bot licks the user instead
    if random.random() < 0.1:  # 10% chance
        battery = random.choice(BATTERY_TYPES)
        number = get_random_number()
        effect = random.choice(LICK_EFFECTS)
        return (
            f"⚡ *REVERSE UNO* ⚡ The bot has licked {nick} like they were a {battery} battery! "
            f"That's {number} users licked in {chan} 👅🔋 {effect}!"
        )

    # Normal battery licking
    battery = random.choice(BATTERY_TYPES)
    adjective = random.choice(LICK_ADJECTIVES)
    number = get_random_number()
    effect = random.choice(LICK_EFFECTS)

    # Rare special events
    if random.random() < 0.05:  # 5% chance for special event
        special_events = [
            f"👅⚡ {nick} {adjective} licked a {battery} battery and achieved MAXIMUM VOLTAGE! "
            f"That's {number} batteries licked in {chan}! {effect}! 🔋⚡🔋⚡🔋",
            f"👅🔋 CRITICAL LICK! {nick} found a SHINY {battery} battery and licked it {adjective}! "
            f"Battery counter overloaded at {number} in {chan}! {effect}! ✨⚡✨",
            f"👅🎆 LEGENDARY LICK! {nick} {adjective} licked the mythical {battery} battery! "
            f"The universe counted {number} battery licks in {chan}! {effect}! 🌟🔋🌟",
            f"👅💥 {nick} {adjective} licked a {battery} battery and opened a portal to the Battery Dimension! "
            f"Interdimensional counter shows {number} licks in {chan}! {effect}! 🌀🔋🌀",
        ]
        return random.choice(special_events)

    # Standard responses with variety
    responses = [
        f"👅🔋 {nick} has {adjective} licked {number} {battery} batteries in {chan} {effect}!",
        f"⚡👅 {nick} {adjective} licks a {battery} battery! Total count: {number} batteries in {chan}! {effect}",
        f"🔋👅 SUCCESS! {nick} has {adjective} completed battery lick #{number} ({battery}) in {chan} {effect}!",
        f"👅⚡ {nick} {adjective} tastes the spicy electrons of a {battery} battery! "
        f"That makes {number} batteries licked in {chan} {effect}!",
        f"🔋💫 {nick} {adjective} licked a {battery} battery! "
        f"Universal battery lick counter for {chan}: {number} {effect}!",
    ]

    return random.choice(responses)


@hook.command("batterystats", "lickstats", autohelp=False)
def battery_stats(text: str, chan: str) -> str:
    """- Show battery licking statistics for this channel"""

    # Generate fake statistics for comedy
    total_licks = get_random_number()
    voltage_consumed = random.randint(1_000_000, 999_999_999)
    electrons_tasted = random.randint(10, 99)

    stats = [
        f"⚡ Battery Licking Statistics for {chan} ⚡",
        f"Total licks: {total_licks}",
        f"Voltage consumed: {voltage_consumed:,}V",
        f"Electrons tasted: {electrons_tasted}×10²³",
        f"Most popular: {random.choice(BATTERY_TYPES)}",
        "Tongues tingled: Yes",
    ]

    return " | ".join(stats)


@hook.command("batteryinfo", autohelp=False)
def battery_info() -> str:
    """- Learn about battery licking safety (Don't actually lick batteries!)"""

    safety_tips = [
        "⚠️ PSA: Please don't actually lick batteries! This is just a silly game! "
        "Real batteries can cause burns, shocks, and chemical poisoning. Stay safe! 🔋❌👅",
        "🔋 Fun Fact: 9V batteries tingle because they can deliver about 500mA through your wet tongue! "
        "But seriously, don't lick real batteries! 👅❌",
        "⚡ Battery Science: The tingle from a 9V battery is your tongue completing the circuit! "
        "Please enjoy this knowledge without practical application! 🧪🔋",
        "👨‍🔬 Did you know? Battery licking was once used to test charge levels! "
        "Modern technology has made this unnecessary and unsafe. Use a multimeter instead! 🔋📊",
    ]

    return random.choice(safety_tips)

