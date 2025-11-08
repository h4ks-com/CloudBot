import random
from dataclasses import dataclass

import httpx

from cloudbot import hook
from plugins.core.webhook_tokens import (
    delete_webhook_token,
    generate_webhook_token,
)

# In-memory storage for active challenges per channel
active_challenges: dict[str, str] = {}
active_tokens: dict[str, str] = {}

CATEGORIES = ["regional", "country", "continental"]


@dataclass
class RandomGameResponse:
    game_id: str
    latitude: float
    longitude: float
    radius_km: float
    size_class: str | None
    share_url: str


@dataclass
class CreateChallengeRequest:
    latitude: float
    longitude: float
    radius_km: float
    size_class: str | None
    webhook_url: str
    webhook_token: str | None
    webhook_extra_params: dict[str, str]


@dataclass
class CreateChallengeResponse:
    challenge_id: str
    game_id: str
    challenge_url: str


@dataclass
class RankingItem:
    username: str
    guess: int
    difference: int
    score: str


@dataclass
class EndChallengeResponse:
    success: bool
    message: str
    actual_population: int
    rankings: list[RankingItem]


class WorldGuessClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    def create_random_game(self, category: str) -> RandomGameResponse:
        response = self.client.post(
            f"{self.base_url}/v1/game/random",
            params={"size_class": category},
        )
        response.raise_for_status()
        data = response.json()
        return RandomGameResponse(**data)

    def create_challenge(
        self, request: CreateChallengeRequest
    ) -> CreateChallengeResponse:
        payload = {
            "latitude": request.latitude,
            "longitude": request.longitude,
            "radius_km": request.radius_km,
            "size_class": request.size_class,
            "webhook_url": request.webhook_url,
            "webhook_extra_params": request.webhook_extra_params,
        }
        if request.webhook_token:
            payload["webhook_token"] = request.webhook_token

        response = self.client.post(
            f"{self.base_url}/v1/challenge/create",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return CreateChallengeResponse(**data)

    def end_challenge(self, challenge_id: str) -> EndChallengeResponse:
        response = self.client.post(
            f"{self.base_url}/v1/challenge/{challenge_id}/end"
        )
        response.raise_for_status()
        data = response.json()
        return EndChallengeResponse(**data)


def get_client(bot) -> WorldGuessClient:
    config = bot.config.get("plugins", {}).get("worldguess", {})
    api_url = config.get("api_url", "http://localhost:8000")
    return WorldGuessClient(api_url)


def get_webhook_url(bot) -> str:
    webhooks_config = bot.config.get("webhooks", {})
    base_url = webhooks_config.get("base_url")
    if not base_url:
        raise ValueError("Webhooks base_url is not configured in bot config")
    return f"{base_url.rstrip('/')}/send_message"


@hook.command("worldguess", "population", "wg", autohelp=False)
def worldguess_cmd(text: str, bot) -> str:
    """[category|list] - Play WorldGuess! Guess population in a circle. Categories: regional, country, continental"""
    client = get_client(bot)

    if not text or text.strip() == "":
        category = random.choice(CATEGORIES)
    elif text.strip().lower() == "list":
        return f"🌍 WorldGuess Categories: {', '.join(CATEGORIES)}"
    else:
        category = text.strip().lower()
        if category not in CATEGORIES:
            return f"❌ Invalid category. Available: {', '.join(CATEGORIES)}"

    try:
        game = client.create_random_game(category)
        return f"🌍 WorldGuess ({category}): {game.share_url}"
    except httpx.HTTPError as e:
        return f"❌ Failed to create game: {e}"


@hook.command(
    "wgc", "worldguesschallenge", "populationchallenge", autohelp=False
)
def worldguess_challenge_cmd(text: str, bot, chan, conn, notice, db) -> str:
    """[category|list] - Create a WorldGuess challenge for the channel. One challenge per channel at a time."""
    if text and text.strip().lower() == "list":
        return f"🌍 Challenge Categories: {', '.join(CATEGORIES)}"

    if chan in active_challenges:
        challenge_id = active_challenges[chan]
        return f"⚠️ Challenge {challenge_id} is already active in this channel. End it first with .wgend"

    category = (
        text.strip().lower()
        if text and text.strip()
        else random.choice(CATEGORIES)
    )
    if category not in CATEGORIES:
        return f"❌ Invalid category. Available: {', '.join(CATEGORIES)}"

    client = get_client(bot)
    webhook_url = get_webhook_url(bot)
    webhook_token = generate_webhook_token(db, expiration_hours=24)

    try:
        game = client.create_random_game(category)

        challenge_request = CreateChallengeRequest(
            latitude=game.latitude,
            longitude=game.longitude,
            radius_km=game.radius_km,
            size_class=game.size_class,
            webhook_url=webhook_url,
            webhook_token=webhook_token,
            webhook_extra_params={"target": chan},
        )

        challenge = client.create_challenge(challenge_request)
        active_challenges[chan] = challenge.challenge_id
        active_tokens[chan] = webhook_token

        return f"🎮 WorldGuess Challenge started! ({category}) - {challenge.challenge_url} - End with .wgend"

    except httpx.HTTPError as e:
        return f"❌ Failed to create challenge: {e}"


@hook.command("wgend", "endworldguess", autohelp=False)
def end_worldguess_challenge_cmd(bot, chan, notice, db) -> str | list[str]:
    """- End the active WorldGuess challenge in this channel"""
    if chan not in active_challenges:
        return "❌ No active challenge in this channel"

    challenge_id = active_challenges[chan]
    client = get_client(bot)

    try:
        result = client.end_challenge(challenge_id)
        del active_challenges[chan]

        # Delete the webhook token if it exists
        if chan in active_tokens:
            delete_webhook_token(db, active_tokens[chan])
            del active_tokens[chan]

        response = [
            f"🏁 Challenge ended! Actual population: {result.actual_population:,}",
            "🏆 Rankings:",
        ]

        for i, ranking in enumerate(result.rankings[:5], 1):
            score_emoji = {"good": "🟢", "meh": "🟡", "bad": "🔴"}.get(
                ranking.get("score", "bad"), "⚪"
            )
            response.append(
                f"{i}. {ranking['username']}: {ranking['guess']:,} "
                f"(off by {ranking['difference']:,}) {score_emoji}"
            )

        if len(result.rankings) > 5:
            response.append(f"...and {len(result.rankings) - 5} more")

        return response

    except httpx.HTTPError as e:
        return f"❌ Failed to end challenge: {e}"


@hook.command("wgstatus", autohelp=False)
def worldguess_status_cmd(chan) -> str:
    """- Check if there's an active WorldGuess challenge in this channel"""
    if chan in active_challenges:
        challenge_id = active_challenges[chan]
        return f"🎮 Active challenge: {challenge_id}"
    return "💤 No active challenge in this channel"
