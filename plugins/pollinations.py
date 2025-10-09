import re
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import requests

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.web import TimeoutSession
from plugins.huggingface import FileIrcResponseWrapper

IMAGE_API = "https://image.pollinations.ai"
TEXT_API = "https://text.pollinations.ai"
MAX_HISTORY_LENGTH = 20

VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


@dataclass
class Message:
    role: str  # "user" or "assistant"
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
    def __init__(self):
        self.session = TimeoutSession()
        self.session.headers.update({"Content-Type": "application/json"})

    def get_image_models(self) -> list[str]:
        response = self.session.get(f"{IMAGE_API}/models")
        response.raise_for_status()
        return response.json()

    def get_text_models(self) -> list[Model]:
        response = self.session.get(f"{TEXT_API}/models")
        response.raise_for_status()
        return [Model(**m) for m in response.json()]

    def generate_image(
        self, prompt: str, model: str | None = None
    ) -> requests.Response:
        url = f"{IMAGE_API}/prompt/{prompt}"
        if model:
            url = f"{url}?model={model}"
        response = self.session.get(url, stream=True)
        response.raise_for_status()
        return response

    def generate_audio(
        self, request: str, voice: str | None = None
    ) -> requests.Response:
        url = f"{TEXT_API}/{request}"
        params = {
            "model": "openai-audio",
            "voice": voice or "alloy",
        }
        response = self.session.get(url, params=params)
        return response

    def generate_text_openai(
        self, messages: list[dict], model: str | None = None
    ) -> dict:
        url = f"{TEXT_API}/openai"
        data = {
            "messages": messages,
            "model": model or "openai",
            "private": True,
        }
        response = self.session.post(url, json=data)
        response.raise_for_status()
        return response.json()


# Global state
pollinations_messages_cache: dict[tuple[str, str], Deque[Message]] = {}


@lru_cache
def get_client():
    return PollinationsClient()


def upload_responses(nick: str, messages: list[Message], header: str) -> str:
    bar = "-" * 80
    lb = "\n"
    text_contents = (
        header
        + "\n" * 4
        + f"{lb}{bar}{lb*2}".join(
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
    """Parse first argument as option if it matches available_options, otherwise treat everything as prompt"""
    parts = text.strip().split(maxsplit=1)
    option = None
    prompt = text.strip()

    if (
        len(parts) > 1
        and available_options
        and parts[0].lower() in available_options
    ):
        option = parts[0].lower()
        prompt = parts[1]

    return option, prompt


def detect_code_blocks(markdown_text: str) -> list[str]:
    """Extract code blocks from markdown text"""
    code_block_pattern = re.compile(r"```\S*(.*)```", re.DOTALL)
    return code_block_pattern.findall(markdown_text)


@hook.on_start()
def on_start():
    global pollinations_messages_cache
    pollinations_messages_cache = {}


@hook.command("plimage")
def plimage_command(text: str, nick: str, chan: str) -> str:
    """<[model] prompt> - Generate an image using Pollinations.AI. Use '.plimage list' to see available models."""
    client = get_client()

    if text.strip().lower() == "list":
        try:
            models = client.get_image_models()
            return "Available models: " + ", ".join(models)
        except Exception as e:
            return f"Error getting models: {e}"

    try:
        models = client.get_image_models()
        model, prompt = parse_args(text, models)
    except Exception:
        model, prompt = None, text.strip()

    try:
        response = client.generate_image(prompt, model)
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
            f.flush()
            image_url = FileIrcResponseWrapper.upload_file(f.name, chan or nick)
        return f"Image for '{prompt}': {image_url}"
    except Exception as e:
        return f"Error generating image: {e}"


@hook.command("plaudio")
def plaudio_command(text: str, nick: str, chan: str) -> str:
    """<[voice] prompt> - Generate audio from text using Pollinations.AI. Use '.plaudio list' to see available voices."""
    if text.strip().lower() == "list":
        return "Available voices: " + ", ".join(VOICES)

    voice, prompt = parse_args(text, VOICES)

    client = get_client()

    audio_response = client.generate_audio(prompt, voice)
    if audio_response.status_code != 200:
        return f"Error generating audio: {audio_response.text}"

    audio_bytes = audio_response.content
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        f.flush()
        audio_url = FileIrcResponseWrapper.upload_file(f.name, chan or nick)
    return f"Audio for '{prompt}': {audio_url}"


def process_text_response(
    response_text: str, nick: str, chan: str, messages: Deque[Message]
) -> str:
    """Process and format text response from API"""
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
def pltext_command(text: str, nick: str, chan: str) -> str:
    """<[model] prompt> - Generate text using Pollinations.AI. Use '.pltext list' to see available models."""
    global pollinations_messages_cache
    client = get_client()

    if text.strip().lower() == "list":
        try:
            models = client.get_text_models()
            return "Available models: " + ", ".join([m.name for m in models])
        except Exception as e:
            return f"Error getting models: {e}"

    try:
        models = client.get_text_models()
        model, prompt = parse_args(text, [m.name for m in models])
    except Exception:
        model, prompt = None, text.strip()

    channick = (chan, nick)
    if channick not in pollinations_messages_cache:
        pollinations_messages_cache[channick] = deque(maxlen=MAX_HISTORY_LENGTH)

    pollinations_messages_cache[channick].append(
        Message(role="user", content=prompt)
    )

    try:
        messages = [
            msg.as_dict() for msg in pollinations_messages_cache[channick]
        ]
        response = client.generate_text_openai(messages, model)
        response_text = response["choices"][0]["message"]["content"]
        pollinations_messages_cache[channick].append(
            Message(role="assistant", content=response_text)
        )

        return process_text_response(
            response_text, nick, chan, pollinations_messages_cache[channick]
        )
    except Exception as e:
        return f"Error generating text: {e}"


@hook.command("plapp")
def plapp_command(text: str, nick: str, chan: str) -> str:
    """<prompt> - Create a single page HTML web app on the fly with Pollinations.AI"""
    global pollinations_messages_cache
    client = get_client()

    channick = (chan, nick)
    if channick not in pollinations_messages_cache:
        pollinations_messages_cache[channick] = deque(maxlen=MAX_HISTORY_LENGTH)

    app_prompt = (
        text
        + "\nMake sure to put everything in a single html file so it can be a single code block meant to be directly used in a browser as it is. Do not explain, just show the code."
    )

    pollinations_messages_cache[channick].append(
        Message(role="user", content=app_prompt)
    )

    try:
        messages = [
            msg.as_dict() for msg in pollinations_messages_cache[channick]
        ]
        response = client.generate_text_openai(messages)
        response_text = response["choices"][0]["message"]["content"]
        pollinations_messages_cache[channick].append(
            Message(role="assistant", content=response_text)
        )

        # Extract code blocks
        code_blocks = detect_code_blocks(response_text)

        if not code_blocks:
            return "No code block found in the response. Try again or see what happened with .plpaste."

        with tempfile.NamedTemporaryFile(suffix=".html") as f:
            with open(f.name, "wb") as file:
                file.write(code_blocks[0].encode("utf-8").strip())
            html_url = FileIrcResponseWrapper.upload_file(f.name, "pl")
            paste_url = html_url.removesuffix(".html") + "/p"
            return f"{paste_url}. Try online: {html_url}"
    except Exception as e:
        return f"Error generating web app: {e}"


@hook.command("plpaste", "pollipaste", autohelp=False)
def plpaste_command(nick: str, chan: str, text: str) -> str:
    """[nick] - Pastes the Pollinations conversation history with nick if specified."""
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


@hook.command("plclear", autohelp=False)
def plclear_command(nick: str, chan: str) -> str:
    """Clear Pollinations conversation history for the current user."""
    global pollinations_messages_cache
    channick = (chan, nick)
    if channick in pollinations_messages_cache:
        del pollinations_messages_cache[channick]
        return f"Cleared conversation history for {nick} in {chan}."
    return f"No conversation history to clear for {nick} in {chan}."
