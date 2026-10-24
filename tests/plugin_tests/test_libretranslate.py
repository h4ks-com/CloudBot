import pytest

from plugins import libretranslate

URL = "https://translate.example"


@pytest.fixture(autouse=True)
def setup(mock_bot, mock_requests):
    mock_bot.config["api_keys"] = {
        "libretranslate": "key",
        "libretranslate_url": URL,
    }
    libretranslate.canonical_lang_by_lowercase.clear()
    mock_requests.add(
        "GET",
        URL + "/languages",
        json=[{"code": "en"}, {"code": "ru"}, {"code": "zh-Hans"}],
    )
    yield
    libretranslate.canonical_lang_by_lowercase.clear()


def run(line: str) -> str | None:
    match = libretranslate.TRANSLATE_RE.match(line)
    assert match
    sent: list[str] = []
    libretranslate.translate_cmd(match, "bob", sent.append)
    return sent[0] if sent else None


def test_target_only_detects_source(mock_requests):
    mock_requests.add(
        "POST",
        URL + "/translate",
        json={
            "translatedText": "привет",
            "detectedLanguage": {"language": "en"},
        },
    )
    assert run("@ru hello") == "<bob (RU)> привет"
    sent = mock_requests.calls[-1].request
    assert sent.headers["x-api-key"] == "key"
    assert b'"source": "auto"' in sent.body
    assert b'"q": "hello"' in sent.body


def test_source_and_target_with_alias(mock_requests):
    mock_requests.add(
        "POST", URL + "/translate", json={"translatedText": "你好"}
    )
    assert run("@en:zh hello") == "<bob (ZH-HANS)> 你好"
    assert b'"target": "zh-Hans"' in mock_requests.calls[-1].request.body


def test_unknown_code_stays_quiet():
    assert run("@bo hi there") is None


def test_already_in_target_language_stays_quiet(mock_requests):
    mock_requests.add(
        "POST",
        URL + "/translate",
        json={
            "translatedText": "hello",
            "detectedLanguage": {"language": "en"},
        },
    )
    assert run("@en hello") is None


def test_service_error_is_reported(mock_requests):
    mock_requests.add("POST", URL + "/translate", status=401, body="no key")
    assert run("@ru hello") == "<bob> translation service error 401: no key"


def test_unloadable_languages_stay_quiet(mock_bot, mock_requests):
    mock_requests.replace("GET", URL + "/languages", status=502, body="down")
    assert run("@ru hello") is None
    mock_bot.config["api_keys"] = {}
    assert run("@ru hello") is None
