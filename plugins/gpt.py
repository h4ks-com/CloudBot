import copy
import importlib
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Literal
from urllib.parse import urlparse

import jwt
import pywikibot
import requests
from markitdown import MarkItDown

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util import formatting
from cloudbot.util.ai_common import (
    APP_HTML_PROMPT_SUFFIX,
    Message,
    RoleType,
    clear_history,
    copy_history,
    detect_code_blocks,
    get_or_create_history,
    truncate_or_paste,
    upload_history,
    upload_html_app,
)
from cloudbot.util.web import get_session
from plugins.huggingface import (
    ALIASES,
    HuggingFaceClient,
    attempt_inference,
    process_response,
)
from plugins.wikis import WIKI_APIS, search

from .youtube import get_client, get_video_info, youtube_re

API_URL = "https://g4f.h4ks.com/api/completions"
MAX_SUMMARIZE_MESSAGES = 1000
AGI_HISTORY_LENGTH = 50
MAX_USER_HISTORY_LENGTH = 32

WIKI = ("wikih4ks", "wh")


def patch_input(wiki_password: str):
    def mock_input(question, password=False, default="", force=False):
        if password:
            return wiki_password
        from pywikibot import input as original_input

        return original_input(
            question, password=password, default=default, force=force
        )

    pywikibot.input = mock_input


@hook.on_start()
def on_start():
    wiki_password = bot.config.get_api_key("wiki_password")
    if not wiki_password:
        return
    patch_input(wiki_password)


def generate_z_ai_token(apikey: str, exp_seconds: int = 3600) -> str:
    try:
        api_id, secret = apikey.split(".")
    except Exception as e:
        raise ValueError(f"Invalid Z.ai API key format: {e}")

    payload = {
        "api_key": api_id,
        "exp": int(round(time.time() * 1000)) + exp_seconds * 1000,
        "timestamp": int(round(time.time() * 1000)),
    }

    return jwt.encode(
        payload,
        secret,
        algorithm="HS256",
        headers={"alg": "HS256", "sign_type": "SIGN"},
    )


def get_completion(messages: list[Message]) -> str:
    z_ai_key = bot.config.get_api_key("z_ai")

    if z_ai_key:
        z_ai_endpoint = bot.config.get("z_ai_endpoint", "https://api.z.ai/api/paas/v4/chat/completions")
        z_ai_model = bot.config.get("z_ai_model", "glm-5.1")

        token = generate_z_ai_token(z_ai_key)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        json_data = {
            "model": z_ai_model,
            "messages": [message.as_dict() for message in messages],
        }

        response = get_session().post(z_ai_endpoint, headers=headers, json=json_data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    else:
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
        }

        json_data = {
            "messages": [message.as_dict() for message in messages],
        }

        response = get_session().post(API_URL, headers=headers, json=json_data)
        response.raise_for_status()
        return response.json()["completion"]


gpt_messages_cache: dict[tuple[str, str], Deque[Message]] = {}


@hook.command("gpt")
def gpt_command(text: str, nick: str, chan: str) -> str:
    """<text> - Get a response from text generating LLM."""
    global gpt_messages_cache

    history = get_or_create_history(gpt_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH)
    history.append(Message(role="user", content=text))
    try:
        response = get_completion(list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"
    history.append(Message(role="assistant", content=response))
    return truncate_or_paste(
        response,
        nick,
        list(history),
        f"{nick}'s GPT conversation in {chan}",
    )


def create_web_app(text: str, history: list[Message] | Deque[Message]) -> str:
    history.append(
        Message(role="user", content=text + APP_HTML_PROMPT_SUFFIX)
    )
    try:
        response = get_completion(list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"

    history.append(Message(role="assistant", content=response))
    code_blocks = detect_code_blocks(response)
    if not code_blocks:
        return "No code block found in the response. Try .gptclear or see what happened with .gptpaste."

    return upload_html_app(code_blocks[0])


@hook.command("gptweb", "gptapp")
def gpt_app(text: str, nick: str, chan: str) -> str:
    """<text> - Create a single page html web app on the fly with gpt"""
    global gpt_messages_cache

    history = get_or_create_history(gpt_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH)
    return create_web_app(text, history)


@hook.command("agiweb", "agiapp")
def agi_app(conn, text: str, nick: str, chan: str) -> str:
    """<text> - Create a single page html web app on the fly with gpt"""
    messages = generate_agi_history(conn, chan)
    return create_web_app(text, messages)


@hook.command("gpth", "gpthistory", "gptpaste", autohelp=False)
def gpt_paste_command(nick: str, chan: str, text: str) -> str:
    """[nick] - Pastes the GPT conversation history with nick if specified."""
    global gpt_messages_cache

    text = text.strip()
    if text:
        nick = text

    channick = (chan, nick)
    if channick in gpt_messages_cache:
        return upload_history(
            nick,
            list(gpt_messages_cache[channick]),
            f"{nick}'s GPT conversation in {chan}",
        )
    return (
        f"No conversation history for {nick}. Start a conversation with .gpt."
    )


@hook.command("gptcopy")
def gpt_copy_command(text: str, nick: str, chan: str) -> str:
    """<user> - Copy another user's conversation history into yours, replacing your own."""
    global gpt_messages_cache

    target = text.strip()
    if not target:
        return "Usage: .gptcopy <user>"

    return copy_history(gpt_messages_cache, chan, nick, target, MAX_USER_HISTORY_LENGTH)


@hook.command("gptclear", autohelp=False)
def gpt_clear_command(nick: str, chan: str) -> str:
    """Clear the conversation cache."""
    return clear_history(gpt_messages_cache, chan, nick)


last_summary = ""


def summarize(
    messages: list[str],
    image: bool,
    nick: str,
    chan: str,
    bot,
    reply,
    what: str = "conversation",
    max_words: int | None = None,
) -> str | list[str] | None:
    global last_summary
    if image:
        question_header = (
            f"Please convert the following {what} into an image prompt for a text generating model with only the main"
            " keywords separated by comma: \n```"
        )
    else:
        question_header = (
            f"Please summarize briefly the following {what}: \n```"
        )

    summarize_body = question_header + "\n".join(messages) + "\n```"

    if max_words:
        summarize_body += f"Use at most {max_words} words."

    try:
        response = get_completion(
            [Message(role="user", content=summarize_body)]
        )
    except requests.HTTPError as e:
        return f"Error: {e}"

    if image:
        api_key = bot.config.get_api_key("huggingface")
        if not api_key:
            return "error: missing api key for huggingface"

        client = HuggingFaceClient([api_key])
        response = attempt_inference(
            client, summarize_body, ALIASES["image"].id, reply
        )
        if isinstance(response, str):
            return formatting.truncate(response, 420)
        return formatting.truncate(process_response(response, chan, nick), 420)
    else:
        last_summary = response
        output = formatting.chunk_str(response.replace("\n", " - "))
        if len(output) > 3:
            paste_url = upload_history(
                nick,
                [Message(role="assistant", content=response)],
                f"{nick}'s GPT summary in {chan}",
            )
            output[2] = (
                formatting.truncate(output[2], 350)
                + " (full response: "
                + paste_url
                + ")"
            )
            return output[:3]
        return output


@hook.command("summarize", "summary", autohelp=False)
def summarize_command(
    bot, reply, text: str, chan: str, nick: str, conn
) -> str | list[str] | None:
    """Summarizes the contents of the last chat messages. Optionally pass a number for max words and nicks to summarize. Sorry yeah if your nick is a number fuck you"""
    image = False
    worcount = None
    if text.strip().lower() == "image":
        image = True

    args = text.split()
    worcount = None
    nicks = []
    for arg in args:
        if arg.strip().lower().isdigit():
            worcount = int(arg)
        else:
            nicks.append(arg.casefold())

    inner = []
    i = 0
    for name, _timestamp, msg in reversed(conn.history[chan]):
        if nicks and name.casefold() not in nicks:
            continue

        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "{}: {}"
        inner.append(fmt.format(name, mod_msg))
        i += 1
        if i >= MAX_SUMMARIZE_MESSAGES:
            break

    messages = list(reversed(inner))
    if not messages:
        reply("Nothing found in history to summarize")
        return
    return summarize(
        messages, image, nick, chan, bot, reply, max_words=worcount
    )


@hook.command("sumsum", "sumsummarize", "sumsummary", autohelp=False)
def sumsum(
    bot, text: str, reply, nick: str, chan: str, conn
) -> str | list[str] | None:
    """Summarizes the last summary"""
    global last_summary
    if not last_summary:
        return "No summary to summarize."
    return summarize(
        [last_summary], False, nick, chan, bot, reply, what="text even more"
    )


agi_messages_cache: Deque[tuple[float, str]] = deque(maxlen=AGI_HISTORY_LENGTH)


def generate_agi_history(conn, chan: str) -> list[Message]:
    global agi_messages_cache
    prefix = conn.config["command_prefix"]

    inner: list[tuple[RoleType, float, str]] = []
    i = 0
    for name, timestamp, msg in reversed(conn.history[chan]):
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
            fmt = "* {}: {}"
        else:
            mod_msg = msg
            fmt = "{}: {}"
        if mod_msg.startswith(prefix):
            continue
        inner.append(("user", timestamp, fmt.format(name, mod_msg)))
        i += 1
        if i >= AGI_HISTORY_LENGTH:
            break

    inner.extend(
        ("assistant", timestamp, msg) for timestamp, msg in agi_messages_cache
    )
    sorted_messages = sorted(inner, key=lambda x: x[1])
    messages = copy.deepcopy(sorted_messages)

    for msg in sorted_messages:
        role = msg[0]
        if role == "assistant":
            messages = messages[1:]
            agi_messages_cache.popleft()
        else:
            break

    messages.insert(
        0,
        (
            "user",
            -1,
            (
                "You are watching a conversation between multiple users in a chatroom and they can interact with you"
                " through the .agi command."
            ),
        ),
    )
    return [Message(role=role, content=text) for role, _, text in messages]


@hook.command("agi", "sentient", autohelp=False)
def gpts_command(
    reply, text: str, nick: str, chan: str, conn
) -> str | list[str] | None:
    """<text> - Get a response from text generating LLM that is aware of the conversation."""
    messages = generate_agi_history(conn, chan)
    try:
        response = get_completion(messages)
    except requests.HTTPError as e:
        return f"Error: {e}"

    output = formatting.chunk_str(response.replace("\n", " - "))
    for message in output:
        agi_messages_cache.append((datetime.timestamp(datetime.now()), message))
    if len(output) > 3:
        paste_url = upload_history(
            nick,
            [Message(role="assistant", content=response)],
            f"GPT conversation in {chan}",
        )
        output[2] = (
            formatting.truncate(output[2], 350)
            + " (full response: "
            + paste_url
            + ")"
        )
        return output[:3]
    return output


@hook.command("agipaste", autohelp=False)
def agi_paste_command(nick: str, conn, chan: str) -> str:
    """Pastes the AGI context window."""
    messages = generate_agi_history(conn, chan)
    return upload_history("", messages, f"AGI conversation in {chan}")


@hook.command("gpredict", "gptpredict", "gptpred", "predict", autohelp=False)
def gpredict_command(
    bot, reply, text: str, chan: str, nick: str, conn
) -> str | list[str] | None:
    """<nick> - Predict what the given user might say next based on their chat history."""
    if not text.strip():
        return "Error: You must provide a nick to predict."

    if len(text.split()) > 1:
        return "Error: Only one nick can be provided."

    target_nick = text.strip().casefold()
    messages = []
    was_user_in_history = False
    for name, _timestamp, msg in reversed(conn.history[chan]):
        if msg.startswith("."):
            continue
        if msg.startswith("\x01ACTION"):
            mod_msg = msg[7:].strip(" \x01")
        else:
            mod_msg = msg

        if name.casefold() == target_nick:
            messages.append(Message(role="assistant", content=mod_msg))
            was_user_in_history = True
        else:
            messages.append(
                Message(role="user", content=f"{name} said: {mod_msg}")
            )

        if len(messages) >= AGI_HISTORY_LENGTH:
            break

    if not was_user_in_history or not messages:
        return f"No chat history found for {text.strip()}."

    messages.reverse()
    messages.insert(
        0,
        Message(
            role="user",
            content=(
                "You are in a conversation with multiple people in a chat. Try to behave relaxed, casual and in"
                " character like another user."
            ),
        ),
    )
    messages.append(
        Message(
            role="user",
            content=f"Continue the conversation responding as {target_nick}. Make sure to stay in character.",
        )
    )
    try:
        response = get_completion(messages)
    except requests.HTTPError as e:
        return f"Error: {e}"

    return f"<{target_nick}> {formatting.truncate_str(response, 350)}"


def edit_wiki(
    bot,
    reply,
    chan: str,
    nick: str,
    prompt: str,
    history: Deque[Message] | list[Message],
) -> str:
    user = bot.config.get_api_key("wiki_username")
    history.append(
        Message(
            role="user",
            content=prompt
            + "\nOutput the result as a mediawiki code block meant for a wiki page. Make sure to use mediawiki markup"
            " or html tags that mediawiki supports in the code block.",
        )
    )
    try:
        response = get_completion(list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"

    code_blocks = detect_code_blocks(response)
    if not code_blocks:
        return "No code block found in the response. Try .gptclear or see what happened with .gptpaste."

    wiki_text = code_blocks[0].strip()
    if not wiki_text:
        return "Error: No text found in the response."

    match = re.search(r"^=+\s*(.*?)\s*=+", wiki_text, re.MULTILINE)
    if match:
        title = match.group(1).strip()
    else:
        return "Error: No title found in the response."

    site = pywikibot.Site(url=WIKI_APIS[WIKI], user=user)
    page = pywikibot.Page(site, title)

    if page.exists():
        reply(f"Editing page at {page.full_url()} ...")
        history[-1].content = (
            f"Please edit the following page content:\n```mediawiki\n{page.text}\n```\n\n{wiki_text}\n"
            + history[-1].content
        )
        try:
            response = get_completion(list(history))
        except requests.HTTPError as e:
            return f"Error: {e}"

        history.append(Message(role="assistant", content=response))
        code_blocks = detect_code_blocks(response)
        if not code_blocks:
            return "No code block found in the response. Try .gptclear or see what happened with .gptpaste."

        wiki_text = code_blocks[0].strip()
        if not wiki_text:
            return "Error: No text found in the response."
    else:
        reply(f"Creating page {page.full_url()} ...")
        history.append(Message(role="assistant", content=response))

    error = "Unknown error"
    for _ in range(3):
        page.text = wiki_text
        try:
            page.save("Edited by GPT bot from irc")
            break
        except Exception as e:
            importlib.reload(pywikibot)
            patch_input(bot.config.get_api_key("wiki_password"))
            site = pywikibot.Site(url=WIKI_APIS[WIKI], user=user)
            page = pywikibot.Page(site, title)
            error = str(e)
    else:
        return f"Error: {error}"

    return search(WIKI, title, chan, nick)


@hook.command("gptwiki", autohelp=False)
def gptwiki(
    bot, reply, text: str, chan: str, nick: str, conn
) -> list[str] | str:
    """<text> - Create or edit a wiki page on demand from AI prompt"""
    global gpt_messages_cache
    history = get_or_create_history(gpt_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH)
    return edit_wiki(bot, reply, chan, nick, text, history)


@hook.command("agiwiki", autohelp=False)
def agiwiki(
    bot, reply, text: str, chan: str, nick: str, conn
) -> list[str] | str:
    """<text> - Create or edit a wiki page on demand from AI prompt"""
    messages = generate_agi_history(conn, chan)
    return edit_wiki(bot, reply, chan, nick, text, messages)


@hook.command("gptsummarize", "gptsum", "gpts", autohelp=False)
def gptsummarize(
    bot, reply, text: str, chan: str, nick: str, conn
) -> list[str] | str:
    """<url> - Summarizes the contents of the given url. Can also be youtube urls or anything supported by https://github.com/microsoft/markitdown"""
    global gpt_messages_cache
    if not text.strip():
        return "Error: You must provide a url to summarize."
    parsed_url = urlparse(text.strip())
    if not (parsed_url.scheme and parsed_url.netloc):
        return "Error: Invalid URL provided. Please provide a valid URL to summarize."

    prompt = None

    if youtube_re.search(parsed_url.geturl()):
        info = get_video_info(get_client(), parsed_url.geturl())
        if info["transcript"]:
            prompt = f"Please summarize the contents of the YouTube video '{info['title']}':\n{info['transcript']}\n\n"

    if prompt is None:
        md = MarkItDown()
        try:
            result = md.convert(parsed_url.geturl(), timeout=15)
        except Exception as e:
            return f"Error: {e}"
        if not result.markdown:
            return "Error: Failed to read and convert the source. Make sure it is a supported url"
        prompt = f"Please briefly summarize the contents of the following text:\n{result.markdown}\n\n"

    history = get_or_create_history(gpt_messages_cache, chan, nick, MAX_USER_HISTORY_LENGTH)
    history.append(Message(role="user", content=prompt))
    try:
        response = get_completion(list(history))
    except requests.HTTPError as e:
        return f"Error: {e}"

    history.append(Message(role="assistant", content=response))
    return truncate_or_paste(
        response,
        nick,
        list(history),
        f"{nick}'s GPT conversation in {chan}",
        prefix="Summary: ",
    )
