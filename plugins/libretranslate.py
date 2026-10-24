"""
@<target> <text>           - translates <text> to <target>, detecting the source language
@<source>:<target> <text>  - translates <text> from <source> to <target>

Translates through a LibreTranslate API behind Kong key-auth. We load its language
list when the plugin starts, so clients see the codes it supports right now.

Requires:
    - libretranslate in api_keys (the Kong key)
    - libretranslate_url in api_keys (defaults to https://translate.t3ks.com)
"""

import logging
import re
from collections.abc import Callable
from typing import Annotated

import requests

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.hook import Arg
from cloudbot.util.web import get_session

logger = logging.getLogger("cloudbot")

LIBRETRANSLATE_URL = "https://translate.t3ks.com"
TIMEOUT = 30
AUTO = "auto"

LANG = r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?"
TRANSLATE_RE = re.compile(rf"^@({LANG})(?::({LANG}))?\s+(.+)$")

LANG_ALIASES = {"zh": "zh-hans", "jp": "ja"}


# We look codes up in lowercase and send LibreTranslate its own casing, e.g. "zh-Hans".
languages_by_lowercase_code: dict[str, str] = {}


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


@hook.on_start()
def load_languages() -> None:
    try:
        langs = _request("GET", "/languages").json()
        codes = {lang["code"].lower(): lang["code"] for lang in langs}
    except (TranslateError, requests.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("[libretranslate] could not load languages: %s", e)
        return
    languages_by_lowercase_code.update(codes)


def language_codes() -> list[str]:
    return sorted(languages_by_lowercase_code.values())


def _ensure_languages() -> None:
    # A failed startup fetch leaves the list empty, so we retry on use.
    if not languages_by_lowercase_code:
        load_languages()


def _lang(code: str) -> str | None:
    _ensure_languages()
    lowered = code.lower()
    return languages_by_lowercase_code.get(LANG_ALIASES.get(lowered, lowered))


def _pair(first: str, second: str | None) -> tuple[str | None, str | None]:
    """Resolves "<target>" or "<source>:<target>" codes to (source, target)."""
    if second is None:
        return AUTO, _lang(first)
    source = AUTO if first.lower() == AUTO else _lang(first)
    return source, _lang(second)


def translate(text: str, target: str, source: str = AUTO) -> tuple[str, str]:
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
def translate_regex(
    match: re.Match, nick: str, message: Callable[[str], None]
) -> None:
    source, target = _pair(match.group(1), match.group(2))
    # "@bob hi" is just someone talking to bob, so we stay quiet on unknown codes.
    if not source or not target:
        return
    try:
        translated, detected = translate(match.group(3), target, source)
    except TranslateError as e:
        message(f"<{nick}> {e}")
        return
    if source == AUTO and detected == target:
        return
    message(f"<{nick} ({target.upper()})> {translated}")


@hook.command("translate", "tran")
def translate_command(
    languages: Annotated[
        str,
        Arg(
            "Target language, or source:target like en:ru",
            rest=False,
            suggestions=language_codes,
        ),
    ],
    text: Annotated[str, Arg("Text to translate")],
) -> str:
    """Translates text, detecting the source language unless given."""
    first, _, second = languages.partition(":")
    source, target = _pair(first, second or None)
    if not source or not target:
        return f"unknown language in {languages!r}, see .langlist"
    try:
        translated, detected = translate(text, target, source)
    except TranslateError as e:
        return str(e)
    return f"({detected} → {target}) {translated}"


@hook.command("langlist", "tlist", autohelp=False)
def langlist() -> str:
    """- lists the languages .translate and @<code> support"""
    _ensure_languages()
    if not languages_by_lowercase_code:
        return "the language list is not available right now"
    return " ".join(language_codes())
