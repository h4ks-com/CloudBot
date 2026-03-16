import re
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Deque

import requests

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.web import TimeoutSession
from plugins.huggingface import FileIrcResponseWrapper

GEN_API = "https://gen.pollinations.ai"
MAX_HISTORY_LENGTH = 20

VOICES = [
    "alloy",
    "echo",
    "fable",
    "onyx",
    "nova",
    "shimmer",
    "ash",
    "ballad",
    "coral",
    "sage",
    "verse",
    "rachel",
    "domi",
    "bella",
    "elli",
    "charlotte",
    "dorothy",
    "sarah",
    "emily",
    "lily",
    "matilda",
    "adam",
    "antoni",
    "arnold",
    "josh",
    "sam",
    "daniel",
    "charlie",
    "james",
    "fin",
    "callum",
    "liam",
    "george",
    "brian",
    "bill",
]


@dataclass
class Message:
    role: str
    content: str
    timestamp: float = datetime.timestamp(datetime.now())

    def as_dict(self):
        return {
            "role": self.role,
            "content": self.content,
        }


@dataclass
class Model:
    name: str
    description: str
    provider: str
    community: bool
    input_modalities: list[str]
    output_modalities: list[str]
    vision: bool
    audio: bool
    pricing: dict[str, float] | None = None
    tier: str | None = None
    aliases: str | None = None
    tools: list[str] | None = None
    reasoning: bool = False
    uncensored: bool = False
    voices: list[str] | None = None
    search: bool = False
    maxInputChars: int | None = None


class PollinationsClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self.base_url = GEN_API
        self.session = TimeoutSession()
        self.session.headers.update({"Content-Type": "application/json"})
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def get_text_models(self) -> list[dict]:
        response = self.session.get(f"{self.base_url}/v1/models")
        response.raise_for_status()
        return response.json()["data"]

    def get_image_models(self) -> list[dict]:
        response = self.session.get(f"{self.base_url}/image/models")
        response.raise_for_status()
        return response.json()

    def generate_text(self, messages: list[dict], model: str = "openai") -> dict:
        url = f"{self.base_url}/v1/chat/completions"
        data = {"model": model, "messages": messages, "stream": False}
        response = self.session.post(url, json=data)
        response.raise_for_status()
        return response.json()

    def generate_image(
        self, prompt: str, model: str | None = None, **kwargs
    ) -> requests.Response:
        url = f"{self.base_url}/image/{prompt}"
        params = {}
        if model:
            params["model"] = model
        params.update(kwargs)
        response = self.session.get(url, params=params, stream=True)
        response.raise_for_status()
        return response

    def generate_audio(self, text: str, voice: str = "alloy") -> requests.Response:
        url = f"{self.base_url}/audio/{text}"
        params = {"model": "elevenlabs", "voice": voice}
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response

    def generate_music(
        self, prompt: str, duration: int = 30, instrumental: bool = True
    ) -> requests.Response:
        url = f"{self.base_url}/audio/{prompt}"
        params = {
            "model": "elevenmusic",
            "duration": str(duration),
            "instrumental": "true" if instrumental else "false",
        }
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response

    def transcribe_audio(
        self, audio_file_path: str, model: str = "whisper-large-v3"
    ) -> dict:
        url = f"{self.base_url}/v1/audio/transcriptions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with open(audio_file_path, "rb") as f:
            files = {"file": ("audio.mp3", f, "audio/mpeg")}
            data = {"model": model, "response_format": "json"}
            response = requests.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        return response.json()

    def get_balance(self) -> dict:
        url = f"{self.base_url}/account/balance"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()


pollinations_messages_cache: dict[tuple[str, str], Deque[Message]] = {}
user_models: dict[tuple[str, str], str] = {}


def get_pollinations_config(bot) -> str | None:
    return bot.config.get("api_keys", {}).get("pollinations")


@lru_cache
def get_client(api_key: str | None = None):
    return PollinationsClient(api_key)


def upload_responses(nick: str, messages: list[Message], header: str) -> str:
    bar = "-" * 80
    lb = "\n"
    text_contents = (
        header
        + "\n" * 4
        + f"{lb}{bar}{lb * 2}".join(
            f"{nick if message.role == 'user' else 'bot'}: {message.content}"
            for message in messages
        )
    )
    with tempfile.NamedTemporaryFile(suffix=".txt") as f:
        with open(f.name, "wb") as file:
            file.write(text_contents.encode("utf-8"))
        file_url = FileIrcResponseWrapper.upload_file(f.name, "pl")
    return file_url


def parse_args(
    text: str, available_options: list[str] | None = None
) -> tuple[str | None, str]:
    parts = text.strip().split(maxsplit=1)
    option = None
    prompt = text.strip()

    if len(parts) > 1 and available_options and parts[0].lower() in available_options:
        option = parts[0].lower()
        prompt = parts[1]

    return option, prompt


def detect_code_blocks(markdown_text: str) -> list[str]:
    code_block_pattern = re.compile(r"```\S*(.*)```", re.DOTALL)
    return code_block_pattern.findall(markdown_text)


@hook.on_start()
def on_start():
    global pollinations_messages_cache, user_models
    pollinations_messages_cache = {}
    user_models = {}


@hook.command("plbalance", autohelp=False)
def plbalance_command(bot, notice) -> str:
    """Check your Pollinations pollen balance."""
    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    client = get_client(api_key)

    try:
        balance_data = client.get_balance()
        balance = balance_data.get("balance", 0)
        return f"Pollen balance: {balance:.2f}"
    except requests.HTTPError as e:
        if e.response.status_code == 401:
            return "Error: Invalid API key"
        return f"Error: {e.response.status_code}"


@hook.command("plmodels", autohelp=False)
def plmodels_command(bot, notice) -> str:
    """List available text generation models."""
    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    client = get_client(api_key)

    try:
        models = client.get_text_models()
        model_ids = [m["id"] for m in models]
        return "Available models: " + ", ".join(model_ids)
    except requests.HTTPError as e:
        return f"Error: {e.response.status_code}"


@hook.command("plmodel", autohelp=False)
def plmodel_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """[model] - Show or set text generation model for this channel."""
    global user_models

    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    current_model = user_models.get((chan, nick), "openai")

    if not text.strip():
        return (
            f"Current model: {current_model}. Use '.plmodels' to list available models."
        )

    new_model = text.strip()
    client = get_client(api_key)

    try:
        models = client.get_text_models()
        model_ids = [m["id"] for m in models]

        if new_model not in model_ids:
            return f"Invalid model '{new_model}'. Use '.plmodels' to list available models."

        user_models[(chan, nick)] = new_model
        return f"Model set to '{new_model}' for {nick} in {chan}."

    except requests.HTTPError as e:
        return f"Error: {e.response.status_code}"


@hook.command("plimage")
def plimage_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<[model] prompt> - Generate an image. Use '.plimage list' to see available models."""
    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    client = get_client(api_key)

    if text.strip().lower() == "list":
        try:
            models = client.get_image_models()
            free_models = [m["name"] for m in models if not m.get("paid_only", False)]
            return "Available free models: " + ", ".join(free_models)
        except requests.HTTPError as e:
            return f"Error: {e.response.status_code}"

    try:
        models = client.get_image_models()
        free_model_names = [m["name"] for m in models if not m.get("paid_only", False)]
        model, prompt = parse_args(text, free_model_names)
    except Exception:
        model, prompt = None, text.strip()

    if model is None:
        model = "flux"

    try:
        response = client.generate_image(prompt, model)
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
            f.flush()
            image_url = FileIrcResponseWrapper.upload_file(f.name, chan or nick)
        return f"Image for '{prompt}': {image_url}"
    except requests.HTTPError as e:
        if e.response.status_code == 402:
            return "Error: Insufficient pollen balance"
        elif e.response.status_code == 403:
            return "Error: Model not available on your plan"
        return f"Error: {e.response.status_code}"


VIDEO_MODELS = {
    "veo": {"max_duration": 8, "default_duration": 6},
    "seedance": {"max_duration": 10, "default_duration": 10},
}


@hook.command("plvideo")
def plvideo_command(text: str, nick: str, chan: str, bot, notice) -> str | None:
    """<[model] prompt> [duration] - Generate video. Models: veo (max 8s), seedance (max 10s, default)"""
    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    prompt = text.strip()
    if not prompt:
        return "Usage: .plvideo <[model] prompt> [duration]"

    model = "seedance"
    duration = VIDEO_MODELS[model]["default_duration"]
    parts = prompt.split()

    if len(parts) > 0 and parts[0].lower() in VIDEO_MODELS:
        model = parts[0].lower()
        prompt = " ".join(parts[1:])
        duration = VIDEO_MODELS[model]["default_duration"]
        parts = prompt.split()

    if len(parts) > 0 and parts[-1].isdigit():
        requested_duration = int(parts[-1])
        max_duration = VIDEO_MODELS[model]["max_duration"]
        if 1 <= requested_duration <= max_duration:
            duration = requested_duration
            prompt = " ".join(parts[:-1])
        else:
            return f"Error: Duration must be between 1 and {max_duration} seconds for {model}"

    if not prompt.strip():
        return "Error: Prompt cannot be empty"

    client = get_client(api_key)

    try:
        response = client.generate_image(prompt, model=model, duration=duration)
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
            f.flush()
            video_url = FileIrcResponseWrapper.upload_file(f.name, chan or nick)
        return f"[{model}] Video for '{prompt}' ({duration}s): {video_url}"
    except requests.HTTPError as e:
        if e.response.status_code == 402:
            return "Error: Insufficient pollen balance"
        elif e.response.status_code == 403:
            return "Error: Model not available on your plan"
        return f"Error: {e.response.status_code}"


@hook.command("plaudio")
def plaudio_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<[voice] text> - Generate TTS audio. Use '.plaudio list' to see available voices."""
    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    if text.strip().lower() == "list":
        return "Available voices: " + ", ".join(VOICES)

    voice, prompt = parse_args(text, VOICES)

    client = get_client(api_key)

    try:
        audio_response = client.generate_audio(prompt, voice or "alloy")
        audio_bytes = audio_response.content
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            f.flush()
            audio_url = FileIrcResponseWrapper.upload_file(f.name, chan or nick)
        return f"Audio for '{prompt}': {audio_url}"
    except requests.HTTPError as e:
        if e.response.status_code == 402:
            return "Error: Insufficient pollen balance"
        elif e.response.status_code == 403:
            return "Error: Voice not available"
        return f"Error: {e.response.status_code}"


@hook.command("plmusic")
def plmusic_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<prompt> [duration] - Generate instrumental music (3-300 seconds, default: 30)."""
    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    parts = text.strip().split()
    if not parts:
        return "Usage: .plmusic <prompt> [duration]"

    duration = 30
    if parts[-1].isdigit():
        duration = int(parts[-1])
        prompt = " ".join(parts[:-1])
        if duration < 3 or duration > 300:
            return "Duration must be between 3 and 300 seconds."
    else:
        prompt = text.strip()

    client = get_client(api_key)

    try:
        music_response = client.generate_music(prompt, duration, instrumental=True)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(music_response.content)
            f.flush()
            music_url = FileIrcResponseWrapper.upload_file(f.name, chan or nick)
        return f"Music for '{prompt}' ({duration}s): {music_url}"

    except requests.HTTPError as e:
        if e.response.status_code == 402:
            return "Error: Insufficient pollen balance"
        elif e.response.status_code == 403:
            return "Error: Music generation not available"
        return f"Error: {e.response.status_code}"


@hook.command("pltranscribe")
def pltranscribe_command(text: str, bot, notice) -> str:
    """<url> - Transcribe audio from URL to text using Whisper."""
    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    url = text.strip()
    if not url:
        return "Usage: .pltranscribe <audio_url>"

    client = get_client(api_key)

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(response.content)
            f.flush()

            result = client.transcribe_audio(f.name)
            return f"Transcription: {result['text']}"

    except requests.HTTPError as e:
        if e.response.status_code == 402:
            return "Error: Insufficient pollen balance"
        elif e.response.status_code == 403:
            return "Error: Transcription not available"
        return f"Error: {e.response.status_code}"
    except requests.Timeout:
        return "Error: Download timeout"


def process_text_response(
    response_text: str, nick: str, chan: str, messages: Deque[Message]
) -> str:
    truncated = formatting.truncate_str(response_text, 350)
    if len(truncated) < len(response_text):
        paste_url = upload_responses(
            nick,
            list(messages),
            f"{nick}'s Pollinations conversation in {chan}",
        )
        return f"{truncated} (full response: {paste_url})"
    return truncated


@hook.command("pltext")
def pltext_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<text> - Generate text using Pollinations AI. Use '.plmodel' to change model."""
    global pollinations_messages_cache, user_models

    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    model = user_models.get((chan, nick), "openai")
    client = get_client(api_key)

    channick = (chan, nick)
    if channick not in pollinations_messages_cache:
        pollinations_messages_cache[channick] = deque(maxlen=MAX_HISTORY_LENGTH)

    pollinations_messages_cache[channick].append(Message(role="user", content=text))

    try:
        messages = [msg.as_dict() for msg in pollinations_messages_cache[channick]]
        response = client.generate_text(messages, model)
        response_text = response["choices"][0]["message"]["content"]
        pollinations_messages_cache[channick].append(
            Message(role="assistant", content=response_text)
        )

        formatted = process_text_response(
            response_text, nick, chan, pollinations_messages_cache[channick]
        )
        return f"[{model}] {formatted}"
    except requests.HTTPError as e:
        if e.response.status_code == 402:
            return "Error: Insufficient pollen balance"
        elif e.response.status_code == 403:
            return f"Error: Model '{model}' not available on your plan"
        return f"Error: {e.response.status_code}"


@hook.command("plapp")
def plapp_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<prompt> - Create a single page HTML web app on the fly with Pollinations AI."""
    global pollinations_messages_cache, user_models

    api_key = get_pollinations_config(bot)
    if not api_key:
        notice("Pollinations API key not configured.")
        return

    model = user_models.get((chan, nick), "openai")
    client = get_client(api_key)

    channick = (chan, nick)
    if channick not in pollinations_messages_cache:
        pollinations_messages_cache[channick] = deque(maxlen=MAX_HISTORY_LENGTH)

    app_prompt = (
        text
        + "\nMake sure to put everything in a single html file so it can be a single code block meant to be directly used in a browser as it is. Do not explain, just show the code formatted inside a markdown code block"
    )

    pollinations_messages_cache[channick].append(
        Message(role="user", content=app_prompt)
    )

    try:
        messages = [msg.as_dict() for msg in pollinations_messages_cache[channick]]
        response = client.generate_text(messages, model)
        response_text = response["choices"][0]["message"]["content"]
        pollinations_messages_cache[channick].append(
            Message(role="assistant", content=response_text)
        )

        code_blocks = detect_code_blocks(response_text)

        if not code_blocks:
            return "No code block found in the response. Try again or see what happened with .plpaste."

        with tempfile.NamedTemporaryFile(suffix=".html") as f:
            with open(f.name, "wb") as file:
                file.write(code_blocks[0].encode("utf-8").strip())
            html_url = FileIrcResponseWrapper.upload_file(f.name, "pl")
            paste_url = html_url.removesuffix(".html") + "/p"
            return f"[{model}] {paste_url}. Try online: {html_url}"
    except requests.HTTPError as e:
        if e.response.status_code == 402:
            return "Error: Insufficient pollen balance"
        elif e.response.status_code == 403:
            return f"Error: Model '{model}' not available on your plan"
        return f"Error: {e.response.status_code}"


@hook.command("plpaste", "pollipaste", autohelp=False)
def plpaste_command(nick: str, chan: str, text: str) -> str:
    """[nick] - Pastes the Pollinations conversation history with nick if specified fy."""
    global pollinations_messages_cache

    text = text.strip()
    if text:
        nick = text

    channick = (chan, nick)
    if channick in pollinations_messages_cache:
        return upload_responses(
            nick,
            list(pollinations_messages_cache[channick]),
            f"{nick}'s Pollinations conversation in {chan}",
        )
    return f"No conversation history for {nick}. Start a conversation with .pltext or .plapp."


@hook.command("plcopy")
def plcopy_command(text: str, nick: str, chan: str) -> str:
    """<user> - Copy another user's conversation history into yours, replacing your own."""
    global pollinations_messages_cache

    target = text.strip()
    if not target:
        return "Usage: .plcopy <user>"

    target_channick = (chan, target)
    if target_channick not in pollinations_messages_cache:
        return f"No conversation history found for {target}."

    pollinations_messages_cache[(chan, nick)] = deque(
        pollinations_messages_cache[target_channick], maxlen=MAX_HISTORY_LENGTH
    )
    return f"Copied {target}'s conversation history into yours ({len(pollinations_messages_cache[(chan, nick)])} messages)."


@hook.command("plclear", autohelp=False)
def plclear_command(nick: str, chan: str) -> str:
    """Clear Pollinations conversation history for the current user."""
    global pollinations_messages_cache
    channick = (chan, nick)
    if channick in pollinations_messages_cache:
        del pollinations_messages_cache[channick]
        return f"Cleared conversation history for {nick} in {chan}."
    return f"No conversation history to clear for {nick} in {chan}."
