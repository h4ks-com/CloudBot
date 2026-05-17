import time
from collections import deque

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.web import get_session

max_length = 100
MAX_CHARS_PER_DAY = 16000

_char_log: deque[tuple[float, int]] = deque()


def _check_daily_chars(n: int) -> str | None:
    now = time.monotonic()
    while _char_log and now - _char_log[0][0] > 86400:
        _char_log.popleft()

    used = sum(c for _, c in _char_log)
    if used + n > MAX_CHARS_PER_DAY:
        return f"Daily Translate cap reached ({used}/{MAX_CHARS_PER_DAY} chars). Resets in 24h."

    _char_log.append((now, n))
    return None


def goog_trans(text, source, target):
    api_key = bot.config.get_api_key("google")
    url = "https://www.googleapis.com/language/translate/v2"

    if len(text) > max_length:
        return "This command only supports input of less then 100 characters."

    cap_msg = _check_daily_chars(len(text))
    if cap_msg:
        return cap_msg

    params = {"q": text, "key": api_key, "target": target, "format": "text"}

    if source:
        params["source"] = source

    request = get_session().get(url, params=params)
    parsed = request.json()

    if parsed.get("error"):
        if parsed["error"]["code"] == 403:
            return "The Translate API is off in the Google Developers Console."

        return "Google API error."

    if not source:
        return "(%(detectedSourceLanguage)s) %(translatedText)s" % (
            parsed["data"]["translations"][0]
        )

    return "%(translatedText)s" % parsed["data"]["translations"][0]


def match_language(fragment):
    fragment = fragment.lower()
    for short, _ in lang_pairs:
        if fragment in short.lower().split():
            return short.split()[0]

    for short, full in lang_pairs:
        if fragment in full.lower():
            return short.split()[0]

    return None


@hook.command("google_translate")
def translate(text):
    """[source language [target language]] <sentence> - translates <sentence> from source language (default autodetect)
    to target language (default English) using Google Translate"""
    api_key = bot.config.get_api_key("google")
    if not api_key:
        return "This command requires a Google API key."

    args = text.split(" ", 2)

    try:
        if len(args) >= 2:
            sl = match_language(args[0])
            if not sl:
                return goog_trans(text, "", "en")
            if len(args) == 2:
                return goog_trans(args[1], sl, "en")
            if len(args) >= 3:
                tl = match_language(args[1])
                if not tl:
                    if sl == "en":
                        return "unable to determine desired target language"
                    return goog_trans(args[1] + " " + args[2], sl, "en")
                return goog_trans(args[2], sl, tl)
        return goog_trans(text, "", "en")
    except OSError as e:
        return e


lang_pairs = [
    ("no", "Norwegian"),
    ("it", "Italian"),
    ("ht", "Haitian Creole"),
    ("af", "Afrikaans"),
    ("sq", "Albanian"),
    ("ar", "Arabic"),
    ("hy", "Armenian"),
    ("az", "Azerbaijani"),
    ("eu", "Basque"),
    ("be", "Belarusian"),
    ("bg", "Bulgarian"),
    ("ca", "Catalan"),
    ("zh-CN zh", "Chinese"),
    ("hr", "Croatian"),
    ("cs", "Czech"),
    ("da", "Danish"),
    ("nl", "Dutch"),
    ("en", "English"),
    ("et", "Estonian"),
    ("tl", "Filipino"),
    ("fi", "Finnish"),
    ("fr", "French"),
    ("gl", "Galician"),
    ("ka", "Georgian"),
    ("de", "German"),
    ("el", "Greek"),
    ("ht", "Haitian Creole"),
    ("iw", "Hebrew"),
    ("hi", "Hindi"),
    ("hu", "Hungarian"),
    ("is", "Icelandic"),
    ("id", "Indonesian"),
    ("ga", "Irish"),
    ("it", "Italian"),
    ("ja jp jpn", "Japanese"),
    ("ko", "Korean"),
    ("lv", "Latvian"),
    ("lt", "Lithuanian"),
    ("mk", "Macedonian"),
    ("ms", "Malay"),
    ("mt", "Maltese"),
    ("no", "Norwegian"),
    ("fa", "Persian"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("sr", "Serbian"),
    ("sk", "Slovak"),
    ("sl", "Slovenian"),
    ("es", "Spanish"),
    ("sw", "Swahili"),
    ("sv", "Swedish"),
    ("th", "Thai"),
    ("tr", "Turkish"),
    ("uk", "Ukrainian"),
    ("ur", "Urdu"),
    ("vi", "Vietnamese"),
    ("cy", "Welsh"),
    ("yi", "Yiddish"),
]
