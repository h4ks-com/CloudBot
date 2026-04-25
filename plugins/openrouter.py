import json
import logging
import re
import time

from cachetools import TTLCache
from openrouter import OpenRouter
from openrouter.components.model import Model, ModelTypedDict
from openrouter.errors import (
    BadRequestResponseError,
    ChatError,
    ForbiddenResponseError,
    PaymentRequiredResponseError,
    ResponseValidationError,
    TooManyRequestsResponseError,
    UnauthorizedResponseError,
)
from sqlalchemy import (
    Column,
    Float,
    PrimaryKeyConstraint,
    String,
    Table,
    delete,
    select,
)

from cloudbot import hook
from cloudbot.util import database, formatting, web
from plugins.agent_tools import upload_markdown_paste

logger = logging.getLogger("cloudbot.openrouter")

llm_user_table = Table(
    "llm_user",
    database.metadata,
    Column("network", String),
    Column("chan", String),
    Column("nick", String),
    Column("selected_model", String),
    Column("created_at", Float),
    Column("updated_at", Float),
    PrimaryKeyConstraint("network", "chan", "nick"),
)

llm_chat_history_table = Table(
    "llm_chat_history",
    database.metadata,
    Column("network", String),
    Column("chan", String),
    Column("nick", String),
    Column("model", String),
    Column("role", String),
    Column("content", String),
    Column("created_at", Float),
    PrimaryKeyConstraint("network", "chan", "nick", "created_at"),
)

DEFAULT_MODEL = "google/gemini-2.0-flash-exp:free"
MAX_HISTORY_MESSAGES = 50
MAX_IRC_LINE_LENGTH = 350

_free_models_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)


def get_user_model(db, network: str, chan: str, nick: str) -> str | None:
    result = db.execute(
        select(llm_user_table.c.selected_model)
        .where(llm_user_table.c.network == network)
        .where(llm_user_table.c.chan == chan)
        .where(llm_user_table.c.nick == nick)
    ).fetchone()
    return result[0] if result else None


def set_user_model(db, network: str, chan: str, nick: str, model: str) -> None:
    current_time = time.time()
    existing = db.execute(
        select(llm_user_table.c.selected_model)
        .where(llm_user_table.c.network == network)
        .where(llm_user_table.c.chan == chan)
        .where(llm_user_table.c.nick == nick)
    ).fetchone()

    if existing:
        db.execute(
            llm_user_table.update()
            .values(selected_model=model, updated_at=current_time)
            .where(llm_user_table.c.network == network)
            .where(llm_user_table.c.chan == chan)
            .where(llm_user_table.c.nick == nick)
        )
    else:
        db.execute(
            llm_user_table.insert().values(
                network=network,
                chan=chan,
                nick=nick,
                selected_model=model,
                created_at=current_time,
                updated_at=current_time,
            )
        )
    db.commit()


def is_free_model(model: Model | ModelTypedDict) -> bool:
    model_id = model["id"] if isinstance(model, dict) else model.id
    return model_id.endswith(":free")


def get_free_models(api_key: str) -> list[str]:
    cache_key = "free_models"
    if cache_key in _free_models_cache:
        return _free_models_cache[cache_key]

    with OpenRouter(api_key=api_key) as client:
        models_response = client.models.list()
        models = (
            models_response.data
            if hasattr(models_response, "data")
            else models_response
        )
        free_models = [model.id for model in models if is_free_model(model)]
        _free_models_cache[cache_key] = free_models
        return free_models


def add_chat_message(
    db, network: str, chan: str, nick: str, model: str, role: str, content: str
) -> None:
    current_time = time.time()

    db.execute(
        llm_chat_history_table.insert().values(
            network=network,
            chan=chan,
            nick=nick,
            model=model,
            role=role,
            content=content,
            created_at=current_time,
        )
    )

    count = db.execute(
        select(llm_chat_history_table.c.created_at)
        .where(llm_chat_history_table.c.network == network)
        .where(llm_chat_history_table.c.chan == chan)
        .where(llm_chat_history_table.c.nick == nick)
    ).fetchall()

    if len(count) > MAX_HISTORY_MESSAGES:
        oldest = db.execute(
            select(llm_chat_history_table.c.created_at)
            .where(llm_chat_history_table.c.network == network)
            .where(llm_chat_history_table.c.chan == chan)
            .where(llm_chat_history_table.c.nick == nick)
            .order_by(llm_chat_history_table.c.created_at.asc())
            .limit(len(count) - MAX_HISTORY_MESSAGES)
        ).fetchall()

        for row in oldest:
            db.execute(
                delete(llm_chat_history_table)
                .where(llm_chat_history_table.c.network == network)
                .where(llm_chat_history_table.c.chan == chan)
                .where(llm_chat_history_table.c.nick == nick)
                .where(llm_chat_history_table.c.created_at == row[0])
            )
    db.commit()


def get_chat_history(db, network: str, chan: str, nick: str) -> list[dict]:
    rows = db.execute(
        select(llm_chat_history_table.c.role, llm_chat_history_table.c.content)
        .where(llm_chat_history_table.c.network == network)
        .where(llm_chat_history_table.c.chan == chan)
        .where(llm_chat_history_table.c.nick == nick)
        .order_by(llm_chat_history_table.c.created_at.asc())
        .limit(MAX_HISTORY_MESSAGES)
    ).fetchall()

    return [{"role": row[0], "content": row[1]} for row in rows]


def clear_chat_history(db, network: str, chan: str, nick: str) -> None:
    db.execute(
        delete(llm_chat_history_table)
        .where(llm_chat_history_table.c.network == network)
        .where(llm_chat_history_table.c.chan == chan)
        .where(llm_chat_history_table.c.nick == nick)
    )
    db.commit()


def format_history_for_paste(
    nick: str, messages: list[dict], header: str
) -> str:
    bar = "-" * 80
    lines = [header, "", bar, ""]
    for msg in messages:
        role = nick if msg["role"] == "user" else "bot"
        lines.append(f"{role}: {msg['content']}")
        lines.append("")
        lines.append(bar)
        lines.append("")
    return "\n".join(lines)


def handle_openrouter_error(e: Exception) -> str:
    logger.debug(f"Full error object: {repr(e)}")
    logger.debug(f"Error attributes: {dir(e)}")

    if isinstance(e, ChatError):
        error_msg = None
        if hasattr(e, "body"):
            try:
                body = e.body
                logger.debug(f"ChatError body: {body}")
                if isinstance(body, str):
                    body_data = json.loads(body)
                else:
                    body_data = body
                if isinstance(body_data, dict) and "error" in body_data:
                    error_info = body_data["error"]
                    if isinstance(error_info, dict):
                        if "metadata" in error_info and isinstance(
                            error_info["metadata"], dict
                        ):
                            error_msg = error_info["metadata"].get("raw")
                        if not error_msg:
                            error_msg = error_info.get(
                                "message", str(error_info)
                            )
                    else:
                        error_msg = str(error_info)
            except (
                json.JSONDecodeError,
                TypeError,
                AttributeError,
                KeyError,
            ) as parse_err:
                logger.debug(f"Error parsing ChatError body: {parse_err}")

        if not error_msg and hasattr(e, "message"):
            error_msg = e.message

        if error_msg:
            return f"API error: {error_msg}"
        return f"API error: {str(e)}"

    if isinstance(e, UnauthorizedResponseError):
        return "Invalid OpenRouter API key."
    if isinstance(e, PaymentRequiredResponseError):
        return "Insufficient API credits."
    if isinstance(e, TooManyRequestsResponseError):
        return "Rate limit exceeded. Try again later."
    if isinstance(e, ForbiddenResponseError):
        return "Access forbidden to this model."
    if isinstance(e, BadRequestResponseError):
        error_msg = None
        if hasattr(e, "body"):
            try:
                body = e.body
                logger.debug(f"BadRequestResponseError body: {body}")
                if isinstance(body, str):
                    body_data = json.loads(body)
                else:
                    body_data = body
                if isinstance(body_data, dict) and "error" in body_data:
                    error_info = body_data["error"]
                    if isinstance(error_info, dict):
                        if "metadata" in error_info and isinstance(
                            error_info["metadata"], dict
                        ):
                            error_msg = error_info["metadata"].get("raw")
                        if not error_msg:
                            error_msg = error_info.get(
                                "message", str(error_info)
                            )
                    else:
                        error_msg = str(error_info)
            except (
                json.JSONDecodeError,
                TypeError,
                AttributeError,
                KeyError,
            ) as parse_err:
                logger.debug(
                    f"Error parsing BadRequestResponseError body: {parse_err}"
                )

        if not error_msg and hasattr(e, "message"):
            error_msg = e.message

        if error_msg:
            return f"Bad request: {error_msg}"
        return f"Bad request: {str(e)}"

    if isinstance(e, ResponseValidationError):
        body = e.body if hasattr(e, "body") else str(e)
        logger.debug(f"ResponseValidationError body: {body}")
        if "error" in str(body).lower():
            try:
                error_data = json.loads(body) if isinstance(body, str) else body
                if isinstance(error_data, dict) and "error" in error_data:
                    error_info = error_data["error"]
                    if isinstance(error_info, dict):
                        if "metadata" in error_info and isinstance(
                            error_info["metadata"], dict
                        ):
                            error_msg = error_info["metadata"].get("raw")
                        else:
                            error_msg = error_info.get(
                                "message", str(error_info)
                            )
                    else:
                        error_msg = str(error_info)
                    return f"API error: {error_msg}"
            except (
                json.JSONDecodeError,
                TypeError,
                AttributeError,
                KeyError,
            ) as parse_err:
                logger.debug(
                    f"Error parsing ResponseValidationError: {parse_err}"
                )
        return f"Response error: {body}"

    logger.debug(f"Unhandled error type: {type(e)}")
    return f"Error: {e}"


@hook.command("llm", autohelp=False)
def llm_chat(text: str, chan: str, conn, db, nick: str) -> str | list[str]:
    """<message> - Chat with AI using your selected model"""
    if not text:
        return "Usage: .llm <message>"

    user_model = get_user_model(db, conn.name, chan, nick)
    model = user_model or conn.bot.config.get("plugins", {}).get(
        "openrouter", {}
    ).get("default_model", DEFAULT_MODEL)

    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured."

    try:
        with OpenRouter(api_key=api_key) as client:
            history = get_chat_history(db, conn.name, chan, nick)
            messages = history[-10:] + [{"role": "user", "content": text}]

            response = client.chat.send(model=model, messages=messages)

            if not hasattr(response, "choices") or not response.choices:
                return "No response from AI."

            choice = response.choices[0]
            if not hasattr(choice, "message") or not hasattr(
                choice.message, "content"
            ):
                return "Invalid response format."

            content = choice.message.content
            if not content:
                return "Empty response from AI."

            # Convert content to string if it's not already
            if not isinstance(content, str):
                content = str(content)

            add_chat_message(db, conn.name, chan, nick, model, "user", text)
            add_chat_message(
                db, conn.name, chan, nick, model, "assistant", content
            )

            flat = " ".join(content.split())
            truncated = formatting.truncate_str(flat, MAX_IRC_LINE_LENGTH)
            if len(truncated) < len(flat) or flat != content.strip():
                full_history = get_chat_history(db, conn.name, chan, nick)
                paste_text = format_history_for_paste(
                    nick, full_history, f"{nick}'s conversation in {chan}"
                )
                paste_url = upload_markdown_paste(
                    paste_text, title=f"{nick}'s conversation in {chan}"
                )
                return f"{truncated} (full: {paste_url})"

            return content

    except (
        ChatError,
        UnauthorizedResponseError,
        PaymentRequiredResponseError,
        TooManyRequestsResponseError,
        ForbiddenResponseError,
        BadRequestResponseError,
        ResponseValidationError,
    ) as e:
        error_type = type(e).__name__
        error_body = getattr(e, "body", None)
        error_msg = getattr(e, "message", str(e))
        logger.error(
            f"OpenRouter API error: type={error_type}, message={error_msg}, body={error_body}"
        )
        return handle_openrouter_error(e)


@hook.command("llmmodel", autohelp=False)
def llm_set_model(text: str, chan: str, conn, db, nick: str) -> str:
    """<model> - Change your AI model"""
    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured."

    if not text:
        current = get_user_model(db, conn.name, chan, nick)
        default = (
            conn.bot.config.get("plugins", {})
            .get("openrouter", {})
            .get("default_model", DEFAULT_MODEL)
        )
        model = current or default
        return f"Current model: {model}. Use .llmlist to see available models."

    try:
        free_models = get_free_models(api_key)
    except (
        ChatError,
        UnauthorizedResponseError,
        PaymentRequiredResponseError,
        TooManyRequestsResponseError,
        ForbiddenResponseError,
        BadRequestResponseError,
        ResponseValidationError,
    ) as e:
        error_type = type(e).__name__
        error_body = getattr(e, "body", None)
        error_msg = getattr(e, "message", str(e))
        logger.error(
            f"Error fetching models: type={error_type}, message={error_msg}, body={error_body}"
        )
        return handle_openrouter_error(e)

    if not free_models:
        return "No free models available."

    if text not in free_models:
        return f"Model '{text}' not available. Use .llmlist to see available models."

    set_user_model(db, conn.name, chan, nick, text)
    return f"Model set to {text}"


@hook.command("llmlist", "llmmodels", autohelp=False)
def llm_list_models(conn) -> str:
    """List available free AI models"""
    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured."

    try:
        free_models = get_free_models(api_key)
    except (
        ChatError,
        UnauthorizedResponseError,
        PaymentRequiredResponseError,
        TooManyRequestsResponseError,
        ForbiddenResponseError,
        BadRequestResponseError,
        ResponseValidationError,
    ) as e:
        error_type = type(e).__name__
        error_body = getattr(e, "body", None)
        error_msg = getattr(e, "message", str(e))
        logger.error(
            f"Error listing models: type={error_type}, message={error_msg}, body={error_body}"
        )
        return handle_openrouter_error(e)

    if not free_models:
        return "No free models available."

    if len(free_models) <= 10:
        return f"Free models: {', '.join(free_models)}"

    models_text = "\n".join(free_models)
    paste_url = web.paste(models_text, ext="txt", raise_on_no_paste=True)
    first_models = ", ".join(free_models[:5])
    return f"Free models: {first_models} (+{len(free_models) - 5} more): {paste_url}"


@hook.command("llmapp", autohelp=False)
def llm_create_app(text: str, chan: str, conn, db, nick: str) -> str:
    """<description> - Create an HTML app"""
    if not text:
        return "Usage: .llmapp <app description>"

    user_model = get_user_model(db, conn.name, chan, nick)
    model = user_model or conn.bot.config.get("plugins", {}).get(
        "openrouter", {}
    ).get("default_model", DEFAULT_MODEL)

    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured."

    app_prompt = (
        f"{text}\n\n"
        "Create a single HTML file with all CSS and JavaScript inline. "
        "The app should be fully functional and self-contained. "
        "Return only the HTML code in a single code block."
    )

    try:
        with OpenRouter(api_key=api_key) as client:
            history = get_chat_history(db, conn.name, chan, nick)
            messages = history[-10:] + [{"role": "user", "content": app_prompt}]

            response = client.chat.send(
                model=model, messages=messages, temperature=0.7, max_tokens=4000
            )

            if not hasattr(response, "choices") or not response.choices:
                return "No response from AI."

            content = response.choices[0].message.content
            if not content:
                return "Empty response from AI."

            # Convert content to string if it's not already
            if not isinstance(content, str):
                content = str(content)

            html_match = re.search(r"```html\n?(.*?)\n?```", content, re.DOTALL)
            if html_match:
                html_content = html_match.group(1).strip()
            else:
                generic_match = re.search(
                    r"```\n?(.*?)\n?```", content, re.DOTALL
                )
                html_content = (
                    generic_match.group(1).strip() if generic_match else content
                )

            html_url = web.paste(
                html_content, ext="html", raise_on_no_paste=True
            )

            add_chat_message(
                db, conn.name, chan, nick, model, "user", app_prompt
            )
            add_chat_message(
                db,
                conn.name,
                chan,
                nick,
                model,
                "assistant",
                f"Created app: {html_url}",
            )

            return f"App created: {html_url}"

    except (
        ChatError,
        UnauthorizedResponseError,
        PaymentRequiredResponseError,
        TooManyRequestsResponseError,
        ForbiddenResponseError,
        BadRequestResponseError,
        ResponseValidationError,
    ) as e:
        error_type = type(e).__name__
        error_body = getattr(e, "body", None)
        error_msg = getattr(e, "message", str(e))
        logger.error(
            f"OpenRouter API error: type={error_type}, message={error_msg}, body={error_body}"
        )
        return handle_openrouter_error(e)


@hook.command("llmpaste", autohelp=False)
def llm_paste_history(chan: str, conn, db, nick: str) -> str:
    """Share your chat history"""
    history = get_chat_history(db, conn.name, chan, nick)
    if not history:
        return "No chat history."

    paste_text = format_history_for_paste(
        nick, history, f"{nick}'s conversation in {chan}"
    )
    paste_url = upload_markdown_paste(
        paste_text, title=f"{nick}'s conversation in {chan}"
    )
    return f"Chat history ({len(history)} messages): {paste_url}"


@hook.command("llmclear", autohelp=False)
def llm_clear_history(chan: str, conn, db, nick: str) -> str:
    """Clear your chat history"""
    clear_chat_history(db, conn.name, chan, nick)
    return "Chat history cleared."
