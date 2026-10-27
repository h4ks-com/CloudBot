import base64
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import requests
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import database, web
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
from plugins.ratelimit import Limit, check, record

MAX_USER_HISTORY_LENGTH = 10
# GGUF weight-file size cap (bytes). 16GB lets through 8B-Q4 / 14B-Q4 / 4B-Q8
# class models — fast enough for IRC. Bigger than that runs at <3 tok/s on
# the homelab iGPU and is too slow for chat.
MODEL_SIZE_LIMIT_BYTES = 16 * 1024**3
STT_DEFAULT_MODEL = "audio-cpp-qwen3-asr"
MODEL_CONFIGS_TTL_S = 3600
# CrowdSec bans a client that requests many distinct pages in a burst, and every model
# has its own config-json URL, so we fetch them no faster than CrowdSec's leak rate.
CONFIG_FETCH_INTERVAL_S = 0.6
IMAGE_DEFAULT_SIZE = "512x512"
# Usecase flags that mark a model as NOT a pure chat LLM. Embedding models also
# advertise FLAG_CHAT, so they must be excluded here or they leak into the chat list.
_NON_CHAT_FLAGS = {
    "FLAG_IMAGE",
    "FLAG_TTS",
    "FLAG_TRANSCRIPT",
    "FLAG_EMBEDDING",
    "FLAG_EMBEDDINGS",
    "FLAG_RERANK",
}
# Inference backends treated as chat-capable LLM runtimes.
_CHAT_BACKENDS = {"llama-cpp", "vulkan-llama-cpp"}

logger = logging.getLogger("cloudbot")

# Every model's config-json, per API base, as (fetched at, {model id: config}).
_model_configs_cache: dict[str, tuple[float, dict[str, dict]]] = {}
_model_configs_lock = threading.Lock()
# Held across a first fetch, so two cold callers never send two bursts.
_model_configs_first_fetch = threading.Lock()
_model_configs_refreshing: set[str] = set()
_allowed_cache: dict[str, tuple[float, list[str]]] = {}


def _rag_personas(bot) -> dict:
    """Optional persona-clone config, read from the (private) bot config so no
    persona text or personal data lives in this public repo. When a configured chat
    model is selected, replies are grounded by retrieval over that person's lines
    held in a LocalAI vector store (embeddings + store + chat all run in LocalAI; the
    bot only orchestrates). Configure in the bot config as:

        plugins.ollama.rag_personas = {
          "<chat-model-id>": {
            "store": "<localai vector store name>",
            "k": 5,
            "persona": "<system prompt describing the persona>"
          }
        }
    """
    return (
        bot.config.get("plugins", {}).get("ollama", {}).get("rag_personas", {})
    )


def _model_is_allowed(api_url: str, headers: dict, mid: str, cfg: dict) -> bool:
    """Judge a model by LocalAI's real per-model metadata. No name regex guessing."""
    usecases = set(cfg.get("known_usecases") or [])
    if "FLAG_CHAT" not in usecases:
        return False
    if usecases & _NON_CHAT_FLAGS:
        return False
    if cfg.get("backend") not in _CHAT_BACKENDS:
        return False

    try:
        est = (
            get_session()
            .post(
                f"{api_url}/api/models/vram-estimate",
                headers=headers,
                json={"model": mid},
                timeout=10,
            )
            .json()
        )
        if est.get("sizeBytes", 0) > MODEL_SIZE_LIMIT_BYTES:
            return False
    except (requests.RequestException, ValueError):
        pass

    return True


class _RateLimited(Exception):
    pass


def _get_config_json(base: str, headers: dict, mid: str) -> dict | None:
    """Fetch a single model's config-json. Raises _RateLimited if server is 429'd.
    Returns None on any other error so callers can skip the model."""
    try:
        r = get_session().get(
            f"{base}/api/models/config-json/{mid}", headers=headers, timeout=10
        )
    except requests.RequestException:
        return None
    if r.status_code == 429:
        raise _RateLimited(f"rate limit hit on {mid}")
    if not r.ok:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _fetch_model_configs(base: str, headers: dict) -> dict[str, dict]:
    response = get_session().get(
        f"{base}/v1/models", headers=headers, timeout=10
    )
    response.raise_for_status()
    configs: dict[str, dict] = {}
    for index, model in enumerate(response.json()["data"]):
        if index:
            time.sleep(CONFIG_FETCH_INTERVAL_S)
        cfg = _get_config_json(base, headers, model["id"])
        if cfg is not None:
            configs[model["id"]] = cfg
    return configs


def _refresh_model_configs(base: str, headers: dict) -> None:
    try:
        configs = _fetch_model_configs(base, headers)
    except (requests.RequestException, _RateLimited, KeyError, ValueError) as e:
        logger.warning("[ollama] could not refresh model configs: %s", e)
        return
    finally:
        with _model_configs_lock:
            _model_configs_refreshing.discard(base)
    with _model_configs_lock:
        _model_configs_cache[base] = (time.monotonic(), configs)


def _model_configs(
    api_url: str, api_key: str | None
) -> tuple[float, dict[str, dict]]:
    """Every model's config, refreshed in the background once stale so callers keep
    the old answer meanwhile. Only the first call after startup waits for a fetch."""
    base = api_url.rstrip("/")
    headers = {"apikey": api_key} if api_key else {}
    with _model_configs_lock:
        cached = _model_configs_cache.get(base)
        stale = (
            cached is None
            or time.monotonic() - cached[0] >= MODEL_CONFIGS_TTL_S
        )
        start_refresh = (
            cached is not None
            and stale
            and base not in _model_configs_refreshing
        )
        if start_refresh:
            _model_configs_refreshing.add(base)
    if cached is None:
        with _model_configs_first_fetch:
            with _model_configs_lock:
                cached = _model_configs_cache.get(base)
            if cached is None:
                configs = _fetch_model_configs(base, headers)
                with _model_configs_lock:
                    cached = _model_configs_cache.setdefault(
                        base, (time.monotonic(), configs)
                    )
        return cached
    if start_refresh:
        threading.Thread(
            target=_refresh_model_configs, args=(base, headers), daemon=True
        ).start()
    return cached


def _fetch_models_by_flag(
    api_url: str, api_key: str | None, flag: str
) -> list[str]:
    """Return model ids whose config advertises the given known_usecase flag."""
    _, configs = _model_configs(api_url, api_key)
    return [
        mid
        for mid, cfg in configs.items()
        if flag in (cfg.get("known_usecases") or [])
    ]


@hook.on_start()
def warm_model_configs(bot: CloudBot) -> None:
    api_url, api_key = (
        bot.config.get("plugins", {}).get("ollama", {}).get(key)
        for key in ("api_url", "api_key")
    )
    if api_url:
        threading.Thread(
            target=_warm_model_configs, args=(api_url, api_key), daemon=True
        ).start()


def _warm_model_configs(api_url: str, api_key: str | None) -> None:
    try:
        _model_configs(api_url, api_key)
    except (requests.RequestException, _RateLimited, KeyError, ValueError) as e:
        logger.warning("[ollama] could not load model configs: %s", e)


def _fetch_image_models(api_url: str, api_key: str | None) -> list[str]:
    return _fetch_models_by_flag(api_url, api_key, "FLAG_IMAGE")


def _fetch_tts_models(api_url: str, api_key: str | None) -> list[str]:
    return _fetch_models_by_flag(api_url, api_key, "FLAG_TTS")


def _fetch_stt_models(api_url: str, api_key: str | None) -> list[str]:
    return _fetch_models_by_flag(api_url, api_key, "FLAG_TRANSCRIPT")


def _fetch_embedding_models(api_url: str, api_key: str | None) -> list[str]:
    return _fetch_models_by_flag(api_url, api_key, "FLAG_EMBEDDINGS")


def _rag_system_message(
    api_url: str, api_key: str | None, cfg: dict, query: str
) -> Message | None:
    """Retrieve the persona's most relevant real lines from its LocalAI vector store
    and return a system message that grounds the reply. ``cfg`` is a persona entry
    from the bot config (store/k/persona). Best-effort: returns None (plain chat) if
    embeddings/store aren't available."""
    base = api_url.rstrip("/")
    headers = {"apikey": api_key} if api_key else {}
    try:
        embed_models = _fetch_embedding_models(base, api_key)
    except (_RateLimited, requests.RequestException):
        return None
    if not embed_models:
        return None
    try:
        emb = (
            get_session()
            .post(
                f"{base}/v1/embeddings",
                headers=headers,
                json={"model": embed_models[0], "input": query},
                timeout=30,
            )
            .json()["data"][0]["embedding"]
        )
        values = (
            get_session()
            .post(
                f"{base}/stores/find",
                headers=headers,
                json={
                    "store": cfg["store"],
                    "key": emb,
                    "topk": cfg.get("k", 5),
                },
                timeout=15,
            )
            .json()
            .get("values", [])
        )
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None
    if not values:
        return Message(role="system", content=cfg["persona"])
    facts = "\n".join(f"- {v}" for v in values)
    return Message(
        role="system",
        content=cfg["persona"]
        + "\n\nFor background, some things you have actually said before - draw on "
        "them only if relevant, never quote them verbatim:\n" + facts,
    )


def _fetch_allowed_models(api_url: str, api_key: str | None) -> list[str]:
    fetched_at, configs = _model_configs(api_url, api_key)
    cached = _allowed_cache.get(api_url)
    if cached and cached[0] == fetched_at:
        return cached[1]
    base = api_url.rstrip("/")
    headers = {"apikey": api_key} if api_key else {}
    allowed = [
        mid
        for mid, cfg in configs.items()
        if _model_is_allowed(base, headers, mid, cfg)
    ]
    _allowed_cache[api_url] = (fetched_at, allowed)
    return allowed


def get_ollama_config(
    bot, model: str | None = None
) -> tuple[str | None, str | None]:
    config = bot.config.get("plugins", {}).get("ollama", {})
    api_url = config.get("api_url")
    api_key = config.get("api_key")
    if not api_url:
        return api_url, api_key

    allowed = _fetch_allowed_models(api_url, api_key)
    if model is None:
        if not allowed:
            raise ValueError("No chat models available on the server.")
        return api_url, api_key
    if model not in allowed:
        raise ValueError(
            f"Model '{model}' is not allowed. Choose from: {', '.join(allowed)}"
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

    url = f"{api_url.rstrip('/')}/v1/chat/completions"
    response = get_session().post(
        url, headers=headers, json=json_data, timeout=60
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


ollama_messages_cache: dict[tuple[str, str], deque[Message]] = {}
user_models: dict[tuple[str, str], str] = {}


@hook.command("ai", "ollama")
def ai_command(
    nick: str,
    chan: str,
    bot: CloudBot,
    event: CommandEvent,
    prompt: Annotated[str, Arg("What to ask")],
) -> str | list[str]:
    """Get a response from Ollama LLM."""
    model = user_models.get((chan, nick))

    try:
        api_url, api_key = get_ollama_config(bot, model)
    except ValueError as e:
        return str(e)
    if not api_url:
        event.notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""
    if model is None:
        model = _fetch_allowed_models(api_url, api_key)[0]

    history = get_or_create_history(
        ollama_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH
    )
    history.append(Message(role="user", content=prompt))
    messages = list(history)
    rag_cfg = _rag_personas(bot).get(model)
    if rag_cfg:
        rag = _rag_system_message(api_url, api_key, rag_cfg, prompt)
        if rag is not None:
            messages = [rag, *messages]
    try:
        response = get_completion(api_url, api_key, model, messages)
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
    history: list[Message] | deque[Message],
    bot,
    api_url: str,
    api_key: str,
    model: str,
) -> str:
    history.append(Message(role="user", content=text + APP_HTML_PROMPT_SUFFIX))
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
def ai_app(
    nick: str,
    chan: str,
    bot: CloudBot,
    event: CommandEvent,
    description: Annotated[str, Arg("The app to build")],
) -> str:
    """Create a single page html web app on the fly with Ollama"""
    model = user_models.get((chan, nick))

    try:
        api_url, api_key = get_ollama_config(bot, model)
    except ValueError as e:
        return str(e)
    if not api_url:
        event.notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""
    if model is None:
        model = _fetch_allowed_models(api_url, api_key)[0]

    history = get_or_create_history(
        ollama_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH
    )
    return create_web_app(description, history, bot, api_url, api_key, model)


@hook.command("aih", "aihistory", "aipaste", autohelp=False)
def ai_paste_command(
    nick: str,
    chan: str,
    user: Annotated[
        str | None, Arg("Whose history to paste", kind="user")
    ] = None,
) -> str:
    """Pastes the Ollama conversation history with nick if specified."""

    if user:
        nick = user

    channick = (chan, nick)
    if channick in ollama_messages_cache:
        return upload_history(
            nick,
            list(ollama_messages_cache[channick]),
            f"{nick}'s Ollama conversation in {chan}",
        )
    return f"No conversation history for {nick}. Start a conversation with .ai."


@hook.command("aicopy")
def ai_copy_command(
    nick: str,
    chan: str,
    user: Annotated[str, Arg("Whose history to copy", kind="user")],
) -> str:
    """Copy another user's conversation history into yours, replacing your own."""
    return copy_history(
        ollama_messages_cache, chan, nick, user, MAX_USER_HISTORY_LENGTH
    )


@hook.command("aiclear", autohelp=False)
def ai_clear_command(nick: str, chan: str) -> str:
    """- Clear the conversation cache."""
    return clear_history(ollama_messages_cache, chan, nick)


@hook.command("aimodels", autohelp=False)
def ai_models_command(bot: CloudBot, event: CommandEvent) -> list[str] | str:
    """- List the Ollama models."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        event.notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return None

    tags_url = f"{api_url.rstrip('/')}/v1/models"

    headers = {}
    if api_key:
        headers["apikey"] = api_key

    try:
        response = get_session().get(tags_url, headers=headers, timeout=10)
        response.raise_for_status()
        models = response.json()["data"]
        all_names = [m["id"] for m in models]
        allowed = _fetch_allowed_models(api_url, api_key)
        return [
            f"Available models: {', '.join(all_names)}",
            f"Allowed chat models (backend=llama-cpp, weights ≤{MODEL_SIZE_LIMIT_BYTES // (1024**3)} GB): {', '.join(allowed)}",
        ]
    except requests.HTTPError as e:
        return f"Error fetching models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"


@hook.command("aisetmodel", "aimodel", "setaimodel", autohelp=False)
def ai_set_model_command(
    nick: str,
    chan: str,
    bot: CloudBot,
    event: CommandEvent,
    model: Annotated[str | None, Arg("Model to use")] = None,
) -> str:
    """Set the Ollama model to use for this user in this channel."""

    if not model:
        current = user_models.get((chan, nick)) or "<server default>"
        return f"You are currently using model '{current}'. Specify a model with this command to change it. Use .aimodels to list allowed."

    try:
        get_ollama_config(bot, model)
    except ValueError as e:
        return str(e)

    channick = (chan, nick)
    user_models[channick] = model
    return f"Ollama model set to '{model}'  {nick} in {chan}."


@hook.command("aimage")
def ai_image_command(
    nick: str,
    chan: str,
    bot: CloudBot,
    event: CommandEvent,
    description: Annotated[
        str, Arg("Optional model then the image prompt, or list")
    ],
) -> str:
    """Generate an image via LocalAI. '.aimage list' to list models."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        event.notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""

    try:
        image_models = _fetch_image_models(api_url, api_key)
    except _RateLimited:
        return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
    except requests.HTTPError as e:
        return f"Error listing image models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"

    if description.lower() == "list":
        if not image_models:
            return (
                "No image models detected. If you installed some, the server may be "
                "rate-limiting per-model metadata probes — use an enterprise API key in the "
                "bot config."
            )
        return f"Available image models: {', '.join(image_models)}"

    if not image_models:
        return (
            "No image models detected. Try '.aimage list' — if the server has models but "
            "they aren't listed, bot is probably rate-limited (use enterprise API key)."
        )

    parts = description.split(None, 1)
    if len(parts) == 2 and parts[0] in image_models:
        model, prompt = parts[0], parts[1]
    else:
        model, prompt = image_models[0], description

    if not prompt:
        return "Usage: .aimage [model] <prompt>"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["apikey"] = api_key

    try:
        response = get_session().post(
            f"{api_url.rstrip('/')}/v1/images/generations",
            headers=headers,
            json={
                "model": model,
                "prompt": prompt,
                "size": IMAGE_DEFAULT_SIZE,
                "n": 1,
                "response_format": "b64_json",
            },
            timeout=180,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        return f"Error: {e}"
    except requests.Timeout:
        return "Error: image generation timed out"

    try:
        png_bytes = base64.b64decode(response.json()["data"][0]["b64_json"])
    except (KeyError, ValueError, TypeError):
        return "Error: unexpected response from server"

    paste_url = web.paste(png_bytes, ext="png")
    return f"[{model}] {paste_url}"


def synthesize_speech(bot, text: str) -> str:
    """Synthesize ``text`` to speech via LocalAI TTS; return the pasted WAV URL
    (or an ``Error: ...`` string). ``text`` may be prefixed with a voice name.
    Shared by the ``.tts`` command and the agent's text_to_speech tool."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        return "Error: Ollama plugin not configured"
    text = text.strip()
    try:
        tts_models = _fetch_tts_models(api_url, api_key)
    except _RateLimited:
        return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
    except requests.HTTPError as e:
        return f"Error listing audio models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"
    if not tts_models:
        return "No TTS models detected."

    parts = text.split(None, 1)
    model, prompt = tts_models[0], text
    if len(parts) == 2:
        first = parts[0].lower()
        match = next((m for m in tts_models if m == parts[0]), None) or next(
            (m for m in tts_models if first in m.lower()), None
        )
        if match:
            model, prompt = match, parts[1]
    if not prompt:
        return "Usage: [voice] <text>"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["apikey"] = api_key
    try:
        response = get_session().post(
            f"{api_url.rstrip('/')}/v1/audio/speech",
            headers=headers,
            json={"model": model, "input": prompt},
            timeout=180,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        return f"Error: {e}"
    except requests.Timeout:
        return "Error: speech synthesis timed out"

    wav_bytes = response.content
    if not wav_bytes or wav_bytes[:4] != b"RIFF":
        return "Error: server did not return a WAV"
    return f"[{model}] {web.paste(wav_bytes, ext='wav')}"


def transcribe_audio(bot, text: str) -> str:
    """Transcribe the audio at a URL via LocalAI Whisper; return the transcript
    (or an ``Error: ...`` string). ``text`` may be prefixed with a model name.
    Shared by the ``.stt`` command and the agent's speech_to_text tool."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        return "Error: Ollama plugin not configured"
    text = text.strip()
    try:
        stt_models = _fetch_stt_models(api_url, api_key)
    except _RateLimited:
        return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
    except requests.HTTPError as e:
        return f"Error listing STT models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"
    if not stt_models:
        return "No STT models detected."

    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0] in stt_models:
        model, audio_url = parts[0], parts[1]
    else:
        # LocalAI lists models in no fixed order, and some transcription models only
        # align known lyrics, so we pick the default by name.
        preferred = (
            bot.config.get("plugins", {}).get("ollama", {}).get("stt_model")
            or STT_DEFAULT_MODEL
        )
        model = preferred if preferred in stt_models else stt_models[0]
        audio_url = text
    audio_url = audio_url.strip()
    if not (
        audio_url.startswith("http://") or audio_url.startswith("https://")
    ):
        return "Usage: [model] <audio_url>"

    try:
        wav = _to_wav(_download_capped(audio_url))
    except AudioError as e:
        return f"Error: {e}"
    headers = {}
    if api_key:
        headers["apikey"] = api_key
    try:
        response = get_session().post(
            f"{api_url.rstrip('/')}/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("audio.wav", wav, "audio/wav")},
            data={"model": model},
            timeout=180,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        return f"Error: {e}"
    except requests.Timeout:
        return "Error: transcription timed out"

    try:
        transcript = response.json().get("text", "").strip()
    except ValueError:
        return "Error: unexpected response from server"
    if not transcript:
        return f"[{model}] (no speech detected)"
    msg = f"[{model}] {transcript}"
    if len(msg) > 400:
        return f"[{model}] {web.paste(transcript, ext='txt')}"
    return msg


@hook.command("aiaudio", "tts")
def ai_audio_command(
    nick: str,
    chan: str,
    bot: CloudBot,
    event: CommandEvent,
    speech: Annotated[
        str, Arg("Optional voice then the text to speak, or list")
    ],
) -> str:
    """Generate speech via LocalAI TTS. '.aiaudio list' to list voices."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        event.notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""
    if speech.lower() == "list":
        try:
            tts_models = _fetch_tts_models(api_url, api_key)
        except _RateLimited:
            return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
        except requests.HTTPError as e:
            return f"Error listing audio models: {e}"
        except requests.Timeout:
            return "Error: Request timed out"
        return (
            f"Available TTS models: {', '.join(tts_models)}"
            if tts_models
            else "No TTS models detected."
        )
    return synthesize_speech(bot, speech)


@hook.command("aistt", "stt")
def ai_stt_command(
    nick: str,
    chan: str,
    bot: CloudBot,
    event: CommandEvent,
    audio_url: Annotated[str, Arg("Audio URL to transcribe, or list")],
) -> str:
    """Transcribe an audio URL via LocalAI Whisper. '.stt list' to list models."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        event.notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""
    if audio_url.lower() == "list":
        try:
            stt_models = _fetch_stt_models(api_url, api_key)
        except _RateLimited:
            return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
        except requests.HTTPError as e:
            return f"Error listing STT models: {e}"
        except requests.Timeout:
            return "Error: Request timed out"
        return (
            f"Available STT models: {', '.join(stt_models)}"
            if stt_models
            else "No STT models detected."
        )
    return transcribe_audio(bot, audio_url)


# Audio understanding. The clip goes through ffmpeg here, so LocalAI only ever
# receives a short 16 kHz mono WAV, whatever format the URL served.
LISTEN_MODELS = ("qwen2.5-omni-7b", "qwen3-omni-30b-a3b-instruct")
LISTEN_DEFAULT_MODEL = LISTEN_MODELS[0]
LISTEN_MAX_SECONDS = 300
LISTEN_MAX_DOWNLOAD_BYTES = 60 * 1024**2
LISTEN_FFMPEG_TIMEOUT_S = 60
LISTEN_DEFAULT_QUESTION = (
    "Describe this audio. Say what it is; for music give the genre, instruments, "
    "vocals and mood; for speech say who speaks and what about."
)
LISTEN_SYSTEM_PROMPT = (
    "You listen to audio for an IRC channel. Answer in plain text without markdown, "
    "in at most three short sentences unless the question asks for more."
)
LISTEN_BUCKET = "listen"
LISTEN_USER_LIMITS = [
    Limit(60, 3, "Rate limited: 3 clips a minute. Try again shortly."),
    Limit(86400, 30, "Daily limit reached: 30 clips a day."),
]
LISTEN_GLOBAL_LIMITS = [
    Limit(
        86400,
        200,
        "The bot has listened to 200 clips today. Try again tomorrow.",
    ),
]


class AudioError(Exception):
    """A message for the user about audio we could not fetch, decode or hear."""


def listen_model(bot) -> str:
    return (
        bot.config.get("plugins", {}).get("ollama", {}).get("listen_model")
        or LISTEN_DEFAULT_MODEL
    )


@contextmanager
def _listen_session() -> Iterator[Session]:
    """The agent calls in from executor threads that own no session, so we close
    each one here and never leave SQLite's single write lock held."""
    db = database.Session()
    try:
        yield db
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    finally:
        database.Session.remove()


def _listen_limit(nick: str) -> str | None:
    with _listen_session() as db:
        return check(
            db, f"{LISTEN_BUCKET}:{nick.lower()}", LISTEN_USER_LIMITS
        ) or check(db, LISTEN_BUCKET, LISTEN_GLOBAL_LIMITS)


def _record_listen(nick: str) -> None:
    with _listen_session() as db:
        record(db, f"{LISTEN_BUCKET}:{nick.lower()}")
        record(db, LISTEN_BUCKET)


def _download_capped(url: str) -> bytes:
    data = bytearray()
    try:
        with get_session().get(url, timeout=30, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(64 * 1024):
                data += chunk
                if len(data) > LISTEN_MAX_DOWNLOAD_BYTES:
                    raise AudioError(
                        f"the file is over {LISTEN_MAX_DOWNLOAD_BYTES // 1024**2} MB"
                    )
    except requests.RequestException as e:
        raise AudioError(f"could not fetch the audio: {e}") from e
    return bytes(data)


def _to_wav(audio: bytes) -> bytes:
    """Decode anything ffmpeg reads into the first LISTEN_MAX_SECONDS as WAV. The
    whitelist keeps ffmpeg on the local file, so a playlist cannot make it fetch
    other URLs from the bot's host. We write to a file because a WAV streamed to a
    pipe carries no data length, which some LocalAI backends refuse."""
    with tempfile.TemporaryDirectory() as workdir:
        source = os.path.join(workdir, "source")
        target = os.path.join(workdir, "audio.wav")
        with open(source, "wb") as f:
            f.write(audio)
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-threads",
                    "1",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-i",
                    source,
                    "-vn",
                    "-t",
                    str(LISTEN_MAX_SECONDS),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    target,
                ],
                capture_output=True,
                timeout=LISTEN_FFMPEG_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise AudioError("decoding the audio took too long") from e
        except FileNotFoundError as e:
            raise AudioError("ffmpeg is not installed on the bot") from e
        wav = b""
        if result.returncode == 0 and os.path.exists(target):
            with open(target, "rb") as f:
                wav = f.read()
    if len(wav) <= 44:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        raise AudioError(
            "could not decode that as audio"
            + (f": {detail[-1]}" if detail else "")
        )
    return wav


def listen_to_audio(
    bot, nick: str, audio_url: str, question: str
) -> tuple[str, str]:
    """Answer a question about the audio at a URL with the global listen model.
    Returns (model, answer); raises AudioError with a message for the user.
    Shared by the ``.listen`` command and the agent's listen_to_audio tool."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        raise AudioError("the Ollama plugin is not configured")
    if not audio_url.startswith(("http://", "https://")):
        raise AudioError("give a public http(s) audio URL")
    limited = _listen_limit(nick)
    if limited:
        raise AudioError(limited)

    wav = _to_wav(_download_capped(audio_url))
    model = listen_model(bot)
    headers = {"apikey": api_key} if api_key else {}
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": LISTEN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64.b64encode(wav).decode(),
                            "format": "wav",
                        },
                    },
                    {
                        "type": "text",
                        "text": question or LISTEN_DEFAULT_QUESTION,
                    },
                ],
            },
        ],
    }
    try:
        response = get_session().post(
            f"{api_url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=180,
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip()
    except requests.Timeout as e:
        raise AudioError("the model took too long to answer") from e
    except requests.RequestException as e:
        raise AudioError(f"the model failed: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise AudioError("the model sent an answer we could not read") from e
    _record_listen(nick)
    return model, answer


@hook.command("listen")
def listen_command(
    nick: str,
    chan: str,
    bot: CloudBot,
    audio_url: Annotated[
        str, Arg("Audio URL, up to 5 minutes are heard", rest=False)
    ],
    question: Annotated[
        str | None, Arg("What to ask about it; describes it by default")
    ] = None,
) -> str | list[str]:
    """Describe an audio clip or answer a question about it."""
    try:
        model, answer = listen_to_audio(bot, nick, audio_url, question or "")
    except AudioError as e:
        return f"Error: {e}"
    asked = question or LISTEN_DEFAULT_QUESTION
    return truncate_or_paste(
        answer,
        nick,
        [
            Message(role="user", content=f"{audio_url}\n{asked}"),
            Message(role="assistant", content=answer),
        ],
        f"{nick}'s audio question in {chan}",
        prefix=f"[{model}] ",
    )


@hook.command("listenmodel", permissions=["botcontrol"], autohelp=False)
def listen_model_command(
    bot: CloudBot,
    model: Annotated[
        str | None,
        Arg("Model .listen uses for everyone", choices=LISTEN_MODELS),
    ] = None,
) -> str:
    """Show or set the model .listen and the agent use to hear audio."""
    if not model:
        return f".listen uses '{listen_model(bot)}'. Choose from: {', '.join(LISTEN_MODELS)}"
    if model not in LISTEN_MODELS:
        return (
            f"Unknown model '{model}'. Choose from: {', '.join(LISTEN_MODELS)}"
        )
    bot.config.setdefault("plugins", {}).setdefault("ollama", {})[
        "listen_model"
    ] = model
    bot.config.save_config()
    return f".listen now uses '{model}' for everyone."
