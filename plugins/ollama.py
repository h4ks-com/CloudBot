import re
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Literal

import requests

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.web import get_session
from plugins.huggingface import FileIrcResponseWrapper

MAX_USER_HISTORY_LENGTH = 10
ALLOWED_MODELS = [
    "qwen2.5-coder:3b",
    "qwen:latest",
    "llama3:latest",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:3b",
    "opencoder:8b",
]
RoleType = Literal["user", "assistant"]


@dataclass
class Message:
    role: RoleType
    content: str
    timestamp: float = datetime.timestamp(datetime.now())

    def as_dict(self):
        return {
            "role": self.role,
            "content": self.content,
        }


def get_ollama_config(bot, model: str | None = None) -> tuple[str | None, str | None]:
    """Get Ollama configuration from bot config."""
    config = bot.config.get("plugins", {}).get("ollama", {})
    api_url = config.get("api_url")
    api_key = config.get("api_key")
    if model is None:
        model = ALLOWED_MODELS[0]
    if model not in ALLOWED_MODELS:
        raise ValueError(f"Model '{model}' is not allowed. Choose from: {', '.join(ALLOWED_MODELS)}")

    return api_url, api_key


def get_completion(api_url: str, api_key: str, model: str, messages: list[Message]) -> str:
    """Call Ollama API to get completion."""
    headers = {
        "Content-Type": "application/json",
    }

    if api_key:
        headers["apikey"] = api_key

    json_data = {
        "model": model,
        "messages": [message.as_dict() for message in messages],
        "stream": False,
    }

    response = get_session().post(api_url, headers=headers, json=json_data, timeout=60)
    response.raise_for_status()
    return response.json()["message"]["content"]


def upload_responses(nick: str, messages: list[Message], header: str) -> str:
    """Upload conversation to pastebin."""
    bar = "-" * 80
    lb = "\n"
    text_contents = (
        header
        + "\n" * 4
        + f"{lb}{bar}{lb*2}".join(
            f"{nick if message.role == 'user' else 'bot'}: {message.content}" for message in messages
        )
    )
    with tempfile.NamedTemporaryFile(suffix=".txt") as f:
        with open(f.name, "wb") as file:
            file.write(text_contents.encode("utf-8"))
        paste_url = FileIrcResponseWrapper.upload_file(f.name, "st")
    return paste_url


def detect_code_blocks(markdown_text: str) -> list[str]:
    """Extract code blocks from markdown."""
    code_block_pattern = re.compile(r"```\S*(.*)```", re.DOTALL)
    block = code_block_pattern.findall(markdown_text)
    if not block:
        code_block_pattern = re.compile(r"```(.*)", re.DOTALL)
        block = code_block_pattern.findall(markdown_text)
        if not block:
            return [markdown_text]
    return block


ollama_messages_cache: dict[tuple[str, str], Deque[Message]] = {}
user_models: dict[tuple[str, str], str] = {}


@hook.command("ai", "ollama")
def ai_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<text> - Get a response from Ollama LLM."""
    global ollama_messages_cache
    global user_models
    model = user_models.get((chan, nick), "qwen:latest")

    # Get configuration
    api_url, api_key = get_ollama_config(bot, model)
    if not api_url:
        notice("Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config.")
        return

    channick = (chan, nick)
    if channick not in ollama_messages_cache:
        ollama_messages_cache[channick] = deque(maxlen=MAX_USER_HISTORY_LENGTH)

    ollama_messages_cache[channick].append(Message(role="user", content=text))
    try:
        response = get_completion(api_url, api_key, model, list(ollama_messages_cache[channick]))
    except requests.HTTPError as e:
        return f"Error: {e}"
    except requests.Timeout:
        return "Error: Request timed out"
    except Exception as e:
        return f"Error: {e}"

    ollama_messages_cache[channick].append(Message(role="assistant", content=response))
    truncated = formatting.truncate_str(response, 350)
    if len(truncated) < len(response):
        paste_url = upload_responses(
            nick,
            list(ollama_messages_cache[channick]),
            f"{nick}'s Ollama conversation in {chan}",
        )
        return f"{truncated} (full response: {paste_url})"
    return truncated


def create_web_app(
    text: str, history: list[Message] | Deque[Message], bot, api_url: str, api_key: str, model: str
) -> str:
    """Create a single-page HTML app using Ollama."""
    history.append(
        Message(
            role="user",
            content=text
            + "\nMake sure to put everything in a single html file so it can be a single code block meant to be"
            " directly used in a browser as it is. Do not explain, just show the code.",
        )
    )
    try:
        response = get_completion(api_url, api_key, model, list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"
    except requests.Timeout:
        return "Error: Request timed out"
    except Exception as e:
        return f"Error: {e}"

    history.append(Message(role="assistant", content=response))
    code_blocks = detect_code_blocks(response)
    if not code_blocks:
        return "No code block found in the response. Try .aiclear or see what happened with .aipaste."

    with tempfile.NamedTemporaryFile(suffix=".html") as f:
        with open(f.name, "wb") as file:
            file.write(code_blocks[0].encode("utf-8").strip())
        html_url = FileIrcResponseWrapper.upload_file(f.name, "st")
        paste_url = html_url.removesuffix(".html") + "/p"
        return f"{paste_url}. Try online: {html_url}"


@hook.command("aiapp", "aiweb")
def ai_app(text: str, nick: str, chan: str, bot, notice) -> str:
    """<text> - Create a single page html web app on the fly with Ollama"""
    global ollama_messages_cache
    global user_models
    model = user_models.get((chan, nick), "qwen:latest")

    # Get configuration
    api_url, api_key = get_ollama_config(bot, model)
    if not api_url:
        notice("Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config.")
        return

    channick = (chan, nick)
    if channick not in ollama_messages_cache:
        ollama_messages_cache[channick] = deque(maxlen=MAX_USER_HISTORY_LENGTH)

    return create_web_app(text, ollama_messages_cache[channick], bot, api_url, api_key, model)


@hook.command("aih", "aihistory", "aipaste", autohelp=False)
def ai_paste_command(nick: str, chan: str, text: str) -> str:
    """[nick] - Pastes the Ollama conversation history with nick if specified."""
    global ollama_messages_cache

    text = text.strip()
    if text:
        nick = text

    channick = (chan, nick)
    if channick in ollama_messages_cache:
        return upload_responses(
            nick,
            list(ollama_messages_cache[channick]),
            f"{nick}'s Ollama conversation in {chan}",
        )
    return f"No conversation history for {nick}. Start a conversation with .ai."


@hook.command("aiclear", autohelp=False)
def ai_clear_command(nick: str, chan: str) -> str:
    """Clear the conversation cache."""
    global ollama_messages_cache

    channick = (chan, nick)
    if channick in ollama_messages_cache:
        ollama_messages_cache.pop(channick)
        return "Conversation cache cleared."
    return "No conversation cache to clear."


@hook.command("aimodels", autohelp=False)
def ai_models_command(bot, notice) -> list[str] | str:
    """List available Ollama models."""
    # Get configuration
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        notice("Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config.")
        return

    # Get base URL (remove /api/chat if present)
    base_url = api_url.rsplit("/api/", 1)[0]
    tags_url = f"{base_url}/api/tags"

    headers = {}
    if api_key:
        headers["apikey"] = api_key

    try:
        response = get_session().get(tags_url, headers=headers, timeout=10)
        response.raise_for_status()
        models = response.json()["models"]
        model_names = [m["name"] for m in models]
        return [
            f"Available models: {', '.join(model_names)}",
            "Allowed models: " + ", ".join(ALLOWED_MODELS),
        ]
    except requests.HTTPError as e:
        return f"Error fetching models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"
    except Exception as e:
        return f"Error: {e}"


@hook.command("aisetmodel", "aimodel", "setaimodel", autohelp=False)
def ai_set_model_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<model> - Set the Ollama model to use for this user in this channel."""
    global user_models

    model = text.strip()
    if not model:
        return f"You are currently using model '{user_models.get((chan, nick), 'qwen:latest')}'. Specify a model with this command to change it."

    # Get configuration
    try:
        get_ollama_config(bot, model)
    except ValueError as e:
        return str(e)

    channick = (chan, nick)
    user_models[channick] = model
    return f"Ollama model set to '{model}'  {nick} in {chan}."
