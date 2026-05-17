import base64
import time
from typing import Deque

import requests

from cloudbot import hook
from cloudbot.util import web
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
# GGUF weight-file size cap (bytes). 16GB lets through 8B-Q4 / 14B-Q4 / 4B-Q8
# class models — fast enough for IRC. Bigger than that runs at <3 tok/s on
# the homelab iGPU and is too slow for chat.
MODEL_SIZE_LIMIT_BYTES = 16 * 1024**3
ALLOWED_MODELS_TTL_S = 300
IMAGE_DEFAULT_SIZE = "512x512"
# Usecase flags that mark a model as NOT a pure chat LLM.
_NON_CHAT_FLAGS = {
    "FLAG_IMAGE",
    "FLAG_TTS",
    "FLAG_TRANSCRIPT",
    "FLAG_EMBEDDING",
    "FLAG_RERANK",
}
# Inference backends treated as chat-capable LLM runtimes.
_CHAT_BACKENDS = {"llama-cpp", "vulkan-llama-cpp"}

_allowed_cache: dict[str, tuple[float, list[str]]] = {}
_image_models_cache: dict[str, tuple[float, list[str]]] = {}
_tts_models_cache: dict[str, tuple[float, list[str]]] = {}
_stt_models_cache: dict[str, tuple[float, list[str]]] = {}


def _model_is_allowed(api_url: str, headers: dict, mid: str) -> bool:
    """Query LocalAI's real per-model metadata. No name regex guessing."""
    cfg = _get_config_json(api_url, headers, mid)
    if cfg is None:
        return False

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


def _fetch_models_by_flag(
    api_url: str,
    api_key: str | None,
    flag: str,
    cache: dict[str, tuple[float, list[str]]],
) -> list[str]:
    """Return model ids whose config advertises the given known_usecase flag."""
    now = time.monotonic()
    cached = cache.get(api_url)
    if cached and now - cached[0] < ALLOWED_MODELS_TTL_S:
        return cached[1]

    base = api_url.rstrip("/")
    headers = {"apikey": api_key} if api_key else {}
    response = get_session().get(
        f"{base}/v1/models", headers=headers, timeout=10
    )
    response.raise_for_status()
    ids = [m["id"] for m in response.json()["data"]]
    matched: list[str] = []
    for mid in ids:
        cfg = _get_config_json(base, headers, mid)
        if cfg is None:
            continue
        if flag in (cfg.get("known_usecases") or []):
            matched.append(mid)

    cache[api_url] = (now, matched)
    return matched


def _fetch_image_models(api_url: str, api_key: str | None) -> list[str]:
    return _fetch_models_by_flag(
        api_url, api_key, "FLAG_IMAGE", _image_models_cache
    )


def _fetch_tts_models(api_url: str, api_key: str | None) -> list[str]:
    return _fetch_models_by_flag(
        api_url, api_key, "FLAG_TTS", _tts_models_cache
    )


def _fetch_stt_models(api_url: str, api_key: str | None) -> list[str]:
    return _fetch_models_by_flag(
        api_url, api_key, "FLAG_TRANSCRIPT", _stt_models_cache
    )


def _fetch_allowed_models(api_url: str, api_key: str | None) -> list[str]:
    now = time.monotonic()
    cached = _allowed_cache.get(api_url)
    if cached and now - cached[0] < ALLOWED_MODELS_TTL_S:
        return cached[1]

    base = api_url.rstrip("/")
    headers = {"apikey": api_key} if api_key else {}
    response = get_session().get(
        f"{base}/v1/models", headers=headers, timeout=10
    )
    response.raise_for_status()
    ids = [m["id"] for m in response.json()["data"]]
    allowed = [mid for mid in ids if _model_is_allowed(base, headers, mid)]

    _allowed_cache[api_url] = (now, allowed)
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


ollama_messages_cache: dict[tuple[str, str], Deque[Message]] = {}
user_models: dict[tuple[str, str], str] = {}


@hook.command("ai", "ollama")
def ai_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<text> - Get a response from Ollama LLM."""
    global ollama_messages_cache
    global user_models
    model = user_models.get((chan, nick))

    try:
        api_url, api_key = get_ollama_config(bot, model)
    except ValueError as e:
        return str(e)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""
    if model is None:
        model = _fetch_allowed_models(api_url, api_key)[0]

    history = get_or_create_history(
        ollama_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH
    )
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
def ai_app(text: str, nick: str, chan: str, bot, notice) -> str:
    """<text> - Create a single page html web app on the fly with Ollama"""
    global ollama_messages_cache
    global user_models
    model = user_models.get((chan, nick))

    try:
        api_url, api_key = get_ollama_config(bot, model)
    except ValueError as e:
        return str(e)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""
    if model is None:
        model = _fetch_allowed_models(api_url, api_key)[0]

    history = get_or_create_history(
        ollama_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH
    )
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

    return copy_history(
        ollama_messages_cache, chan, nick, target, MAX_USER_HISTORY_LENGTH
    )


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
def ai_set_model_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<model> - Set the Ollama model to use for this user in this channel."""
    global user_models

    model = text.strip()
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
def ai_image_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<[model] prompt> - Generate an image via LocalAI. '.aimage list' to list models."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""

    text = text.strip()

    try:
        image_models = _fetch_image_models(api_url, api_key)
    except _RateLimited:
        return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
    except requests.HTTPError as e:
        return f"Error listing image models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"

    if text.lower() == "list":
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

    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0] in image_models:
        model, prompt = parts[0], parts[1]
    else:
        model, prompt = image_models[0], text

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


@hook.command("aiaudio", "tts")
def ai_audio_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<[voice] text> - Generate speech via LocalAI TTS. '.aiaudio list' to list voices."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""

    text = text.strip()

    try:
        tts_models = _fetch_tts_models(api_url, api_key)
    except _RateLimited:
        return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
    except requests.HTTPError as e:
        return f"Error listing audio models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"

    if text.lower() == "list":
        if not tts_models:
            return "No TTS models detected."
        return f"Available TTS models: {', '.join(tts_models)}"

    if not tts_models:
        return "No TTS models detected. Try '.aiaudio list'."

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
        return "Usage: .aiaudio [voice] <text>"

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

    paste_url = web.paste(wav_bytes, ext="wav")
    return f"[{model}] {paste_url}"


@hook.command("aistt", "stt")
def ai_stt_command(text: str, nick: str, chan: str, bot, notice) -> str:
    """<audio_url> - Transcribe an audio URL via LocalAI Whisper. '.stt list' to list models."""
    api_url, api_key = get_ollama_config(bot)
    if not api_url:
        notice(
            "Ollama plugin not configured. Please set 'plugins.ollama.api_url' in config."
        )
        return ""

    text = text.strip()

    try:
        stt_models = _fetch_stt_models(api_url, api_key)
    except _RateLimited:
        return "Error: server rate-limited enumeration — bot likely needs enterprise API key."
    except requests.HTTPError as e:
        return f"Error listing STT models: {e}"
    except requests.Timeout:
        return "Error: Request timed out"

    if text.lower() == "list":
        if not stt_models:
            return "No STT models detected."
        return f"Available STT models: {', '.join(stt_models)}"

    if not stt_models:
        return "No STT models detected. Try '.stt list'."

    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0] in stt_models:
        model, audio_url = parts[0], parts[1]
    else:
        model, audio_url = stt_models[0], text

    audio_url = audio_url.strip()
    if not (
        audio_url.startswith("http://") or audio_url.startswith("https://")
    ):
        return "Usage: .stt [model] <audio_url>"

    try:
        audio_bytes = get_session().get(audio_url, timeout=30).content
    except requests.RequestException as e:
        return f"Error fetching audio: {e}"

    headers = {}
    if api_key:
        headers["apikey"] = api_key

    try:
        response = get_session().post(
            f"{api_url.rstrip('/')}/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("audio", audio_bytes)},
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
