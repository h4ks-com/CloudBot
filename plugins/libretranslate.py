"""
@<target> <text>           - translates <text> to <target>, detecting the source language
@<source>:<target> <text>  - translates <text> from <source> to <target>

Translates through a LibreTranslate API behind Kong key-auth.

Requires:
    - libretranslate in api_keys (the Kong key)
    - libretranslate_url in api_keys (defaults to https://translate.t3ks.com)
"""

import logging
import re
from collections.abc import Callable

import requests

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session

logger = logging.getLogger("cloudbot")

LIBRETRANSLATE_URL = "https://translate.t3ks.com"
TIMEOUT = 30

LANG = r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?"
TRANSLATE_RE = re.compile(rf"^@({LANG})(?::({LANG}))?\s+(.+)$")

LANG_ALIASES = {"zh": "zh-hans", "jp": "ja"}

canonical_lang_by_lowercase: dict[str, str] = {}


class TranslateError(Exception):
    pass


def _base_url() -> str:
    return (
        bot.config.get_api_key("libretranslate_url") or LIBRETRANSLATE_URL
    ).rstrip("/")


def _request(
    method: str, path: str, json: dict[str, str] | None = None
) -> requests.Response:
    api_key = bot.config.get_api_key("libretranslate")
    if not api_key:
        raise TranslateError("missing the libretranslate API key")
    try:
        response = get_session(timeout=TIMEOUT).request(
            method,
            _base_url() + path,
            headers={"x-api-key": api_key},
            json=json,
        )
    except requests.RequestException as e:
        raise TranslateError(f"translation service unreachable: {e}") from e
    if not response.ok:
        raise TranslateError(
            f"translation service error {response.status_code}: {response.text[:200]}"
        )
    return response


def _lang(code: str) -> str | None:
    if not canonical_lang_by_lowercase:
        # "@bob hi" is just someone talking to bob, so we stay quiet when we cannot tell.
        try:
            langs = _request("GET", "/languages").json()
        except (TranslateError, requests.JSONDecodeError) as e:
            logger.warning("[libretranslate] could not load languages: %s", e)
            return None
        canonical_lang_by_lowercase.update(
            {lang["code"].lower(): lang["code"] for lang in langs}
        )
    lowered = code.lower()
    return canonical_lang_by_lowercase.get(LANG_ALIASES.get(lowered, lowered))


def translate(text: str, target: str, source: str = "auto") -> tuple[str, str]:
    """Translates text and returns (translation, source language)."""
    response = _request(
        "POST",
        "/translate",
        json={"q": text, "source": source, "target": target, "format": "text"},
    )
    try:
        result = response.json()
        translated = result["translatedText"]
    except (requests.JSONDecodeError, KeyError) as e:
        raise TranslateError(
            f"unexpected translation response: {response.text[:200]}"
        ) from e
    detected = result.get("detectedLanguage", {}).get("language", source)
    return translated, detected


@hook.regex(TRANSLATE_RE)
def translate_cmd(
    match: re.Match, nick: str, message: Callable[[str], None]
) -> None:
    first, second, text = match.group(1), match.group(2), match.group(3)
    source = _lang(first) if second else "auto"
    target = _lang(second or first)
    if not source or not target:
        return
    try:
        translated, detected = translate(text, target, source)
    except TranslateError as e:
        message(f"<{nick}> {e}")
        return
    if source == "auto" and detected == target:
        return
    message(f"<{nick} ({target.upper()})> {translated}")
