from typing import Deque

import requests

from cloudbot import hook
from cloudbot.util.ai_common import (
    APP_HTML_PROMPT_SUFFIX,
    Message,
    clear_history,
    copy_history,
    detect_code_blocks,
    get_or_create_history,
    truncate_or_paste,
    upload_history,
    upload_html_app,
)
from cloudbot.util.web import get_session

MAX_USER_HISTORY_LENGTH = 10
ALLOWED_MODELS = [
    "qwen2.5-coder:3b",
    "qwen:latest",
    "llama3:latest",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:3b",
    "opencoder:8b",
    "olmo2:7b",
]

def get_ollama_config(
    bot, model: str | None = None
) -> tuple[str | None, str | None]:
    config = bot.config.get("plugins", {}).get("ollama", {})
    api_url = config.get("api_url")
    api_key = config.get("api_key")
    if model is None:
        model = ALLOWED_MODELS[0]
    if model not in ALLOWED_MODELS:
        raise ValueError(
            f"Model '{model}' is not allowed. Choose from: {', '.join(ALLOWED_MODELS)}"
        )

    return api_url, api_key


def get_completion(
    api_url: str, api_key: str, model: str, messages: list[Message]
) -> str:
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

    response = get_session().post(
        api_url, headers=headers, json=json_data, timeout=60
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


ollama_messages_cache: dict[tuple[str, str], Deque[Message]] = {}
user_models: dict[tuple[str, str], str] = {}


@hook.command("ai", "ollama")
def ai_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<text> - Get a response from Ollama LLM."""
    global ollama_messages_cache
    global user_models
    model = user_models.get((chan, nick), "qwen:latest")

    api_url, api_key = get_ollama_config(bot, model)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return

    history = get_or_create_history(ollama_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH)
    history.append(Message(role="user", content=text))
    try:
        response = get_completion(api_url, api_key, model, list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"
    except requests.Timeout:
        return "Error: Request timed out"

    history.append(Message(role="assistant", content=response))
    return truncate_or_paste(
        response,
        nick,
        list(history),
        f"{nick}'s Ollama conversation in {chan}",
        prefix=f"[{model}] ",
    )


def create_web_app(
    text: str,
    history: list[Message] | Deque[Message],
    bot,
    api_url: str,
    api_key: str,
    model: str,
) -> str:
    history.append(
        Message(role="user", content=text + APP_HTML_PROMPT_SUFFIX)
    )
    try:
        response = get_completion(api_url, api_key, model, list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"
    except requests.Timeout:
        return "Error: Request timed out"

    history.append(Message(role="assistant", content=response))
    code_blocks = detect_code_blocks(response)
    if not code_blocks:
        return "No code block found in the response. Try .aiclear or see what happened with .aipaste."

    return upload_html_app(code_blocks[0], model_prefix=model)


@hook.command("aiapp", "aiweb")
def ai_app(text: str, nick: str, chan: str, bot, notice) -> str:
    """<text> - Create a single page html web app on the fly with Ollama"""
    global ollama_messages_cache
    global user_models
    model = user_models.get((chan, nick), "qwen:latest")

    api_url, api_key = get_ollama_config(bot, model)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return

    history = get_or_create_history(ollama_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH)
    return create_web_app(text, history, bot, api_url, api_key, model)


@hook.command("aih", "aihistory", "aipaste", autohelp=False)
def ai_paste_command(nick: str, chan: str, text: str) -> str:
    """[nick] - Pastes the Ollama conversation history with nick if specified."""
    global ollama_messages_cache

    text = text.strip()
    if text:
        nick = text

    channick = (chan, nick)
    if channick in ollama_messages_cache:
        return upload_history(
            nick,
            list(ollama_messages_cache[channick]),
            f"{nick}'s Ollama conversation in {chan}",
        )
    return f"No conversation history for {nick}. Start a conversation with .ai."


@hook.command("aicopy")
def ai_copy_command(text: str, nick: str, chan: str) -> str:
    """<user> - Copy another user's conversation history into yours, replacing your own."""
    global ollama_messages_cache

    target = text.strip()
    if not target:
        return "Usage: .aicopy <user>"

    return copy_history(ollama_messages_cache, chan, nick, target, MAX_USER_HISTORY_LENGTH)


@hook.command("aiclear", autohelp=False)
def ai_clear_command(nick: str, chan: str) -> str:
    """Clear the conversation cache."""
    return clear_history(ollama_messages_cache, chan, nick)


@hook.command("aimodels", autohelp=False)
def ai_models_command(bot, notice) -> list[str] | str:
    """List available Ollama models."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return

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


@hook.command("aisetmodel", "aimodel", "setaimodel", autohelp=False)
def ai_set_model_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<model> - Set the Ollama model to use for this user in this channel."""
    global user_models

    model = text.strip()
    if not model:
        return f"You are currently using model '{user_models.get((chan, nick), 'qwen:latest')}'. Specify a model with this command to change it."

    try:
        get_ollama_config(bot, model)
    except ValueError as e:
        return str(e)

    channick = (chan, nick)
    user_models[channick] = model
    return f"Ollama model set to '{model}'  {nick} in {chan}."
