import pytest

from cloudbot.util.command_args import build_spec
from plugins import libretranslate
from plugins.core import bot_cmds

URL = "https://translate.example"


@pytest.fixture(autouse=True)
def setup(mock_bot, mock_requests):
    mock_bot.config["api_keys"] = {
        "libretranslate": "key",
        "libretranslate_url": URL,
    }
    libretranslate.languages_by_lowercase_code.clear()
    mock_requests.add(
        "GET",
        URL + "/languages",
        json=[
            {"code": "en", "name": "English"},
            {"code": "ru", "name": "Russian"},
            {"code": "zh-Hans", "name": "Chinese"},
        ],
    )
    yield
    libretranslate.languages_by_lowercase_code.clear()


def _mock_translation(
    mock_requests, translated: str, detected: str | None = None
):
    body: dict[str, object] = {"translatedText": translated}
    if detected:
        body["detectedLanguage"] = {"language": detected}
    mock_requests.add("POST", URL + "/translate", json=body)


def run(line: str) -> str | None:
    match = libretranslate.TRANSLATE_RE.match(line)
    assert match
    sent: list[str] = []
    libretranslate.translate_regex(match, "bob", sent.append)
    return sent[0] if sent else None


def test_target_only_detects_source(mock_requests):
    _mock_translation(mock_requests, "привет", detected="en")
    assert run("@ru hello") == "<bob (RU)> привет"
    sent = mock_requests.calls[-1].request
    assert sent.headers["x-api-key"] == "key"
    assert b'"source": "auto"' in sent.body
    assert b'"q": "hello"' in sent.body


def test_source_and_target_with_alias(mock_requests):
    _mock_translation(mock_requests, "你好")
    assert run("@en:zh hello") == "<bob (ZH-HANS)> 你好"
    assert b'"target": "zh-Hans"' in mock_requests.calls[-1].request.body


def test_unknown_code_stays_quiet():
    assert run("@bo hi there") is None


def test_already_in_target_language_stays_quiet(mock_requests):
    _mock_translation(mock_requests, "hello", detected="en")
    assert run("@en hello") is None


def test_service_error_is_reported(mock_requests):
    mock_requests.add("POST", URL + "/translate", status=401, body="no key")
    assert run("@ru hello") == "<bob> translation service error 401: no key"


def test_unloadable_languages_stay_quiet(mock_bot, mock_requests):
    mock_requests.replace("GET", URL + "/languages", status=502, body="down")
    assert run("@ru hello") is None
    mock_bot.config["api_keys"] = {}
    assert run("@ru hello") is None


def test_command_detects_source(mock_requests):
    _mock_translation(mock_requests, "привет мир", detected="en")
    assert (
        libretranslate.translate_command("ru", "hello world")
        == "(en → ru) привет мир"
    )


def test_command_takes_source_target_pair(mock_requests):
    _mock_translation(mock_requests, "hello")
    assert (
        libretranslate.translate_command("ru:en", "привет") == "(ru → en) hello"
    )
    assert b'"source": "ru"' in mock_requests.calls[-1].request.body


def test_command_rejects_unknown_language():
    assert (
        libretranslate.translate_command("xx", "hello")
        == "unknown language in 'xx', see .langlist"
    )


def test_command_args_suggest_loaded_languages():
    libretranslate.load_languages()
    spec = build_spec(libretranslate.translate_command)
    assert spec.parse_text("en:ru good morning all") == {
        "languages": "en:ru",
        "text": "good morning all",
    }
    option = bot_cmds._build_option(spec.args[0])
    assert option.get("choices") == ["en", "ru", "zh-Hans"]


def test_langlist_lists_codes():
    assert libretranslate.langlist() == "en ru zh-Hans"
