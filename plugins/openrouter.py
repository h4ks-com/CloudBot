import json
import logging
import time

from cachetools import TTLCache
from openrouter import OpenRouter
from openrouter.components.model import Model, ModelTypedDict
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

logger = logging.getLogger("cloudbot.openrouter")

# Database tables
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
    Column("messages", String),
    Column("created_at", Float),
    PrimaryKeyConstraint("network", "chan", "nick", "created_at"),
)

# Constants
DEFAULT_MODEL = "z-ai/glm-4.5-air:free"
MAX_HISTORY_MESSAGES = 50


def get_user_model(db, network: str, chan: str, nick: str) -> str | None:
    """Get user's selected model from database."""
    result = db.execute(
        select([llm_user_table.c.selected_model])
        .where(llm_user_table.c.network == network)
        .where(llm_user_table.c.chan == chan)
        .where(llm_user_table.c.nick == nick)
    ).fetchone()

    return result[0] if result else None


def set_user_model(db, network: str, chan: str, nick: str, model: str) -> None:
    """Set user's selected model in database."""
    current_time = time.time()

    # Update existing record or insert new one
    existing = db.execute(
        select([llm_user_table.c.selected_model, llm_user_table.c.created_at])
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


def safe_float(value: str | None) -> int:
    """Convert value to int safely, returning 0 on failure."""
    if value is None:
        return -1
    try:
        return int(value)
    except (ValueError, TypeError):
        return -1


def is_free_model(model: Model | ModelTypedDict) -> bool:
    """Check if model is free by checking all pricing values are 0."""
    id = model["id"] if isinstance(model, dict) else model.id
    return id.endswith(":free")
    # if isinstance(model, dict):
    #     pricing = model.get("pricing", {})
    #     return (
    #         safe_float(pricing.get("prompt")) == 0
    #         and safe_float(pricing.get("completion")) == 0
    #         and safe_float(pricing.get("request")) == 0
    #         and safe_float(pricing.get("image")) == 0
    #     )
    # else:
    #     pricing = model.pricing
    #     return (
    #         safe_float(pricing.prompt) == 0
    #         and safe_float(pricing.completion) == 0
    #         and safe_float(pricing.request) == 0
    #         and safe_float(pricing.image) == 0
    #     )


# TTL cache for free models (1 hour, maxsize=1 for single API key)
_free_models_cache = TTLCache(maxsize=1, ttl=3600)


def get_free_models(api_key: str) -> list[str]:
    """Fetch list of free models from OpenRouter API with TTL caching."""
    # Try to get from cache first
    cache_key = f"free_models_{api_key}"
    if cache_key in _free_models_cache:
        return _free_models_cache[cache_key]

    # Cache miss, fetch new data
    try:
        with OpenRouter(api_key=api_key) as client:
            models_response = client.models.list()
            models = models_response.data if hasattr(models_response, "data") else models_response
            free_models = []
            for model in models:
                if is_free_model(model):
                    free_models.append(model.id)

            # Store in cache
            _free_models_cache[cache_key] = free_models
            return free_models
    except Exception as e:
        logger.error(f"Error fetching free models: {str(e)}")
        return [f"Error fetching free models: {str(e)}"]


def add_chat_message(db, network: str, chan: str, nick: str, model: str, messages: list[dict]) -> None:
    """Add chat message to history."""
    current_time = time.time()

    # Get existing history for this user
    existing_history = db.execute(
        select([llm_chat_history_table.c.messages])
        .where(llm_chat_history_table.c.network == network)
        .where(llm_chat_history_table.c.chan == chan)
        .where(llm_chat_history_table.c.nick == nick)
        .order_by(llm_chat_history_table.c.created_at.desc())
        .limit(MAX_HISTORY_MESSAGES)
    ).fetchall()

    # Add new message
    db.execute(
        llm_chat_history_table.insert().values(
            network=network,
            chan=chan,
            nick=nick,
            model=model,
            messages=json.dumps(messages),
            created_at=current_time,
        )
    )

    # Clean up old messages if exceeding limit
    if len(existing_history) >= MAX_HISTORY_MESSAGES:
        # Get the oldest message to remove
        oldest_to_remove = len(existing_history) - MAX_HISTORY_MESSAGES + 1
        old_messages = db.execute(
            select([llm_chat_history_table.c.created_at])
            .where(llm_chat_history_table.c.network == network)
            .where(llm_chat_history_table.c.chan == chan)
            .where(llm_chat_history_table.c.nick == nick)
            .order_by(llm_chat_history_table.c.created_at.asc())
            .limit(oldest_to_remove)
        ).fetchall()

        # Delete old messages
        for old_msg in old_messages:
            db.execute(
                delete(llm_chat_history_table)
                .where(llm_chat_history_table.c.network == network)
                .where(llm_chat_history_table.c.chan == chan)
                .where(llm_chat_history_table.c.nick == nick)
                .where(llm_chat_history_table.c.created_at == old_msg[0])
            )
    db.commit()


def get_chat_history(db, network: str, chan: str, nick: str) -> list[dict]:
    """Get user's chat history."""
    history = db.execute(
        select([llm_chat_history_table.c.messages, llm_chat_history_table.c.model, llm_chat_history_table.c.created_at])
        .where(llm_chat_history_table.c.network == network)
        .where(llm_chat_history_table.c.chan == chan)
        .where(llm_chat_history_table.c.nick == nick)
        .order_by(llm_chat_history_table.c.created_at.desc())
        .limit(MAX_HISTORY_MESSAGES)
    ).fetchall()

    # Parse JSON messages back to list
    chat_history = []
    for row in history:
        try:
            messages = json.loads(row[0])
            if isinstance(messages, list):
                chat_history.extend(messages)
        except (json.JSONDecodeError, TypeError):
            continue

    return chat_history


def clear_chat_history(db, network: str, chan: str, nick: str) -> None:
    """Clear user's chat history."""
    db.execute(
        delete(llm_chat_history_table)
        .where(llm_chat_history_table.c.network == network)
        .where(llm_chat_history_table.c.chan == chan)
        .where(llm_chat_history_table.c.nick == nick)
    )


def upload_responses(nick: str, messages: list[dict], header: str) -> str:
    """Upload chat history to pastebin."""
    bar = "-" * 80
    lb = "\n"
    text_contents = (
        header
        + "\n" * 4
        + f"{lb}{bar}{lb*2}".join(
            f"{nick if message.get('role') == 'user' else 'bot'}: {message.get('content', '')}" for message in messages
        )
    )
    return web.paste(text_contents, ext="txt", raise_on_no_paste=True)


@hook.command("llm", autohelp=False)
def llm_chat(text: str, chan: str, conn, db, nick: str) -> str:
    """<message> - Chat with AI using your selected model"""
    if not text:
        return "Please provide a message to chat with the AI."

    # Get user's selected model or use default
    user_model = get_user_model(db, conn.name, chan, nick)
    model = user_model or conn.bot.config.get("plugins", {}).get("openrouter", {}).get("default_model", DEFAULT_MODEL)

    # Get API key
    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured. Ask bot admin to add 'openrouter' to api_keys in config.json."

    try:
        with OpenRouter(api_key=api_key) as client:
            # Get chat history for context
            chat_history = get_chat_history(db, conn.name, chan, nick)

            # Prepare messages for API
            messages = []
            if chat_history:
                # Add recent history messages (limit to last 10 for context)
                for msg in chat_history[-10:]:
                    if isinstance(msg, dict) and "role" in msg and "content" in msg:
                        messages.append(msg)

            # Add current user message
            messages.append({"role": "user", "content": text})

            # Send to OpenRouter
            response = client.chat.send(
                model=model,
                messages=messages,
            )

            # Check if response has choices and extract content
            if not hasattr(response, "choices") or not response.choices:
                return "Error: No response from AI model."

            # Store the interaction in history
            choice = response.choices[0]
            if not hasattr(choice, "message") or not hasattr(choice.message, "content"):
                return "Error: Invalid response format from AI model."

            assistant_message = {"role": "assistant", "content": choice.message.content}
            add_chat_message(db, conn.name, chan, nick, model, [assistant_message])

            # Truncate long responses and paste to pastebin
            response_content = choice.message.content
            truncated = formatting.truncate_str(response_content, 350)
            if len(truncated) < len(response_content):
                # Get full chat history for pastebin
                full_history = get_chat_history(db, conn.name, chan, nick)
                paste_url = upload_responses(nick, full_history, f"{nick}'s OpenRouter conversation in {chan}")
                return [f"{truncated} (full response: {paste_url})"]

            return response_content

    except Exception as e:
        error_msg = str(e)
        logger.error(f"OpenRouter API error: {error_msg}")

        # Handle specific API errors
        if "Provider returned error" in error_msg:
            return f"OpenRouter API error: {error_msg}"
        elif "choices" in error_msg.lower():
            return f"Response format error: {error_msg}"
        elif "No response from AI model" in error_msg:
            return f"No response received from model: {model}"
        elif "Invalid API key" in error_msg:
            return "Invalid OpenRouter API key. Please ask bot admin to configure it."
        elif "Model not found" in error_msg:
            return "Model not found. Available models: .llmlist"
        elif "rate limit" in error_msg.lower():
            return "Rate limit exceeded. Please try again later."
        elif "insufficient credits" in error_msg.lower():
            return "Insufficient API credits. Please check your OpenRouter account."
        else:
            return f"Error chatting with AI: {error_msg}"


@hook.command("llmmodel", autohelp=False)
def llm_set_model(text: str, chan: str, conn, db, nick: str) -> str:
    """<model> - Change your AI model"""
    if not text:
        current_model = get_user_model(db, conn.name, chan, nick)
        if current_model:
            return f"Your current model: {current_model}. Available models: .llmlist"
        else:
            return f"No model set for you. Using default model {conn.bot.config.get('plugins', {}).get('openrouter', {}).get('default_model', DEFAULT_MODEL)}. Available models: .llmlist"

    # Get available free models
    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured."

    free_models = get_free_models(api_key)
    if not free_models:
        return "No free models available at the moment."

    # Check if requested model is in free models
    if text not in free_models:
        return f"Model '{text}' not available. Free models: {', '.join(free_models[:5])}"

    # Set user's model preference
    set_user_model(db, conn.name, chan, nick, text)

    return f"Model changed to {text}. Your preference has been saved."


@hook.command("llmlist", "llmmodels", autohelp=False)
def llm_list_models(chan: str, conn) -> str:
    """List available free AI models"""
    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured."

    try:
        free_models = get_free_models(api_key)
        if not free_models:
            return "No free models available at the moment."

        if len(free_models) <= 10:
            model_list = ", ".join(free_models)
            return f"Available free models: {model_list}"

        # Paste all models to pastebin and show first 10 + paste URL
        models_text = "\n".join(free_models)
        paste_url = web.paste(models_text, ext="txt", raise_on_no_paste=True)

        first_10 = ", ".join(free_models[:10])
        return f"Available free models: {first_10} (and {len(free_models) - 10} more) - Full list: {paste_url}"
    except Exception as e:
        logger.error(f"Error listing models: {str(e)}")
        return f"Error listing models: {str(e)}"


@hook.command("llmapp", autohelp=False)
def llm_create_app(text: str, chan: str, conn, db, nick: str) -> str:
    """<app_description> - Create and share AI app"""
    if not text:
        return "Please provide a description for the app you want to create."

    # Get user's selected model or use default
    user_model = get_user_model(db, conn.name, chan, nick)
    model = user_model or conn.bot.config.get("plugins", {}).get("openrouter", {}).get("default_model", DEFAULT_MODEL)

    # Get API key
    api_key = conn.bot.config.get_api_key("openrouter")
    if not api_key:
        return "OpenRouter API key not configured."

    try:
        with OpenRouter(api_key=api_key) as client:
            # Get chat history for context
            chat_history = get_chat_history(db, conn.name, chan, nick)

            # Prepare messages for API
            messages = []
            if chat_history:
                # Add recent history messages (limit to last 10 for context)
                for msg in chat_history[-10:]:
                    if isinstance(msg, dict) and "role" in msg and "content" in msg:
                        messages.append(msg)

            # Add app creation prompt
            app_prompt = (
                text + "\n\nCreate a single HTML file with all CSS and JavaScript inline. "
                "The app should be fully functional and self-contained. "
                "Return only the HTML code in a single code block, no explanations."
            )

            messages.append({"role": "user", "content": app_prompt})

            # Send to OpenRouter
            response = client.chat.send(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
            )

            # Extract HTML from response
            content = response.choices[0].message.content

            # Look for HTML code blocks
            import re

            code_block_pattern = re.compile(r"```html\n?(.*?)\n?```", re.DOTALL)
            match = code_block_pattern.search(content)

            if match:
                html_content = match.group(1).strip()
            else:
                # Fallback: try to extract any code block
                generic_code_pattern = re.compile(r"```\n?(.*?)\n?```", re.DOTALL)
                generic_match = generic_code_pattern.search(content)
                if generic_match:
                    html_content = generic_match.group(1).strip()
                else:
                    html_content = content

            # Upload to pastebin
            html_url = web.paste(html_content, ext="html", raise_on_no_paste=True)
            paste_url = html_url.removesuffix(".html") + "/p"

            # Store the interaction in history
            assistant_message = {"role": "assistant", "content": f"Created app: {paste_url}"}
            add_chat_message(db, conn.name, chan, nick, model, [assistant_message])

            return f"App created: {paste_url} - Try online: {html_url}"

    except Exception as e:
        return f"Error creating app: {str(e)}"


@hook.command("llmpaste", autohelp=False)
def llm_paste_history(chan: str, conn, db, nick: str) -> str:
    """Share your chat history"""
    # Get user's chat history
    chat_history = get_chat_history(db, conn.name, chan, nick)
    if not chat_history:
        return "No chat history to share."

    # Upload to pastebin
    paste_url = upload_responses(nick, chat_history, f"{nick}'s OpenRouter conversation in {chan}")

    return f"Chat history ({len(chat_history)} messages): {paste_url}"


@hook.command("llmclear", autohelp=False)
def llm_clear_history(chan: str, conn, db, nick: str) -> str:
    """Clear your chat history"""
    clear_chat_history(db, conn.name, chan, nick)
    return "Chat history cleared. Your model preference has been kept."
