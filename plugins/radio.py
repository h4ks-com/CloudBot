import logging
import threading
from typing import Any
from urllib.parse import urlparse

import httpx
import requests
from cachetools import TTLCache

from cloudbot import hook
from cloudbot.webhooks.handlers import register_webhook_handler
from cloudbot.bot import bot
from plugins.core.chan_track import get_users

logger = logging.getLogger("cloudbot")

last_sent_messages: dict[str, dict[str, Any]] = {}
stream_token_cache = TTLCache(maxsize=1000, ttl=3600)


def check_user_authenticated(conn, nick):
    """
    Check if a user is authenticated with services.
    Returns None if authenticated, error message if not.
    """
    if not nick or not conn:
        return "🚫 Unable to verify user authentication status. 🚫"

    try:
        user = get_users(conn).getuser(nick)
        if not user.account:
            return "🚫 This command requires you to be authenticated with services (e.g., NickServ). Please identify and try again. 🚫"
    except Exception:
        return "🚫 Unable to verify your authentication status. 🚫"

    return None


def get_radio_url(config: dict[str, Any]) -> str:
    """Extract the base radio URL from config (preserves port, removes paths)."""
    api_url = config.get("api_url", "")
    parsed = urlparse(api_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def fetch_current_metadata(config: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch current playing metadata from radio API."""
    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        return None

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    try:
        response = requests.get(f"{api_url}/metadata/now", headers=headers, timeout=5.0)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch radio metadata: %s", e)
        return None


def format_livestream_message(event_type: str, metadata_response: dict[str, Any], radio_url: str) -> str | None:
    """Format livestream event with current metadata."""
    source = metadata_response.get("source", "unknown")
    metadata = metadata_response.get("metadata", {})
    title = metadata.get("title", "Unknown Track")
    artist = metadata.get("artist", "Unknown Artist")
    show_name = metadata.get("show_name")
    show_user = metadata.get("show_user")

    if event_type == "livestream_started":
        # Build livestream info with show details if available
        if source == "livestream" and (show_name or show_user):
            parts = ["🎬"]

            # Add user info if available
            if show_user and show_user != "unknown":
                parts.append(f"{show_user} is now live!")
            else:
                parts.append("Livestream started")

            # Add show name if available
            if show_name and show_name != "unknown":
                parts.append(f"- {show_name}.")

            # Add track info if meaningful
            if title and title not in ("Testing Stream", "Unknown Track"):
                parts.append(f"♫ {artist} - {title}")

            # Add radio URL
            parts.append(f"| Listen: {radio_url}")

            return " ".join(parts)
        elif source == "user":
            return f"🎬 Livestream started: ♫ {artist} - {title} | Listen: {radio_url}"
        return f"📺 Livestream started | Listen: {radio_url}"
    elif event_type == "livestream_ended":
        if source == "fallback":
            return f"📻 Livestream ended, back to fallback: ♫ {artist} - {title}"
        elif source == "user":
            return f"🎵 Livestream ended, now playing: ♫ {artist} - {title}"
        return "📺 Livestream ended"
    return None


def send_debounced_message(bot_instance: Any, chan: str, event_type: str) -> None:
    """Fetch metadata after delay and send message if different from last."""
    config = bot_instance.config.get("plugins", {}).get("radio", {})
    connection_name = config.get("connection", "gobot")

    metadata_response = fetch_current_metadata(config)
    if not metadata_response:
        return

    radio_url = get_radio_url(config)
    message = format_livestream_message(event_type, metadata_response, radio_url)
    if not message:
        return

    metadata = metadata_response.get("metadata", {})
    current_state = {
        "title": metadata.get("title"),
        "artist": metadata.get("artist"),
        "source": metadata_response.get("source"),
        "event": event_type,
    }

    last_state = last_sent_messages.get(chan, {})
    if (
        last_state.get("title") == current_state["title"]
        and last_state.get("artist") == current_state["artist"]
        and last_state.get("source") == current_state["source"]
    ):
        logger.debug("Skipping duplicate message for %s", chan)
        return

    connection = bot_instance.connections.get(connection_name)
    if connection and connection.connected:
        connection.message(chan, message)
        last_sent_messages[chan] = current_state
        logger.info("Sent debounced message to %s: %s", chan, message)


def handle_radio_webhook(bot_instance: Any, payload: dict[str, Any]) -> None:
    """Handle incoming webhook events from radio streaming service."""
    config = bot_instance.config.get("plugins", {}).get("radio", {})
    channels_config = config.get("channels", {})
    connection_name = config.get("connection", "gobot")

    event_type = payload.get("event_type", "")

    if event_type in ("livestream_started", "livestream_ended"):
        for chan, chan_config in channels_config.items():
            allowed_events = chan_config.get("events", [])
            if event_type in allowed_events:
                timer = threading.Timer(
                    5.0, send_debounced_message, args=(bot_instance, chan, event_type)
                )
                timer.daemon = True
                timer.start()
                logger.debug(
                    "Scheduled debounced message for %s in channel %s", event_type, chan
                )
        return

    message = format_radio_message(event_type, payload)
    if not message:
        return

    connection = bot_instance.connections.get(connection_name)
    if not connection or not connection.connected:
        logger.warning(
            "Connection %s not available for radio webhook", connection_name
        )
        return

    # Webhook sends flat structure with "playlist" field
    data = payload.get("data", {})
    current_state = {
        "title": data.get("title"),
        "artist": data.get("artist"),
        "source": data.get("playlist"),
        "event": event_type,
    }

    for chan, chan_config in channels_config.items():
        allowed_events = chan_config.get("events", [])
        if event_type in allowed_events:
            last_state = last_sent_messages.get(chan, {})
            if (
                last_state.get("title") == current_state["title"]
                and last_state.get("artist") == current_state["artist"]
                and last_state.get("event") == event_type
            ):
                logger.debug("Skipping duplicate immediate message for %s", chan)
                continue

            connection.message(chan, message)
            last_sent_messages[chan] = current_state
            logger.info("Sent immediate message to %s: %s", chan, message)


def format_radio_message(event_type: str, payload: dict[str, Any]) -> str | None:
    """Format webhook payload into IRC message."""
    if event_type == "song_changed":
        data = payload.get("data", {})
        # Webhook sends "playlist" field, not "source"
        playlist = data.get("playlist", "unknown")
        # Title and artist are flat in data, not nested in metadata
        title = data.get("title")
        artist = data.get("artist")

        # Skip if both are empty/None (but allow one to be missing)
        if not title and not artist:
            return None

        # Use fallbacks for missing values
        title = title or "Unknown Track"
        artist = artist or "Unknown Artist"

        # Map playlist to source terminology for display
        if playlist == "user":
            return f"🎵 Now playing: ♫ {artist} - {title}"
        elif playlist == "fallback":
            return f"📻 Fallback playing: ♫ {artist} - {title}"
    elif event_type == "queue_switched":
        from_source = payload.get("data", {}).get("from_source", "unknown")
        to_source = payload.get("data", {}).get("to_source", "unknown")

        if from_source == "user" and to_source == "fallback":
            return "🔄 Queue empty, switched to fallback"
        elif from_source == "fallback" and to_source == "user":
            return "🔄 Switched from fallback to user queue"
        else:
            return f"🔄 Source changed: {from_source} → {to_source}"
    return None


@hook.command("radio", autohelp=False)
def radio(bot: Any) -> str:
    """- Shows what's currently playing on the radio"""
    config = bot.config.get("plugins", {}).get("radio", {})
    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        logger.error("Radio plugin not configured: missing api_url")
        return "Radio not configured. Set 'api_url' in config.json under plugins.radio"

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    response = requests.get(f"{api_url}/metadata/now", headers=headers, timeout=5.0)
    response.raise_for_status()
    data = response.json()

    source = data.get("source", "unknown")
    metadata = data.get("metadata", {})
    title = metadata.get("title", "Unknown Track")
    artist = metadata.get("artist", "Unknown Artist")
    genre = metadata.get("genre")
    description = metadata.get("description")
    show_name = metadata.get("show_name")
    show_user = metadata.get("show_user")

    if source == "fallback":
        emoji = "📻"
        source_text = "Fallback"
    elif source == "user":
        emoji = "🎵"
        source_text = "Queue"
    elif source == "livestream":
        emoji = "🎬"
        source_text = "Live"
    else:
        emoji = "🎶"
        source_text = source.title()

    parts = [f"{emoji} Now Playing [{source_text}]:"]

    # For livestreams, show user and show name if available
    if source == "livestream":
        if show_user and show_user != "unknown":
            parts.append(f"👤 {show_user}")
        if show_name and show_name != "unknown":
            parts.append(f"📺 {show_name}")

    parts.append(f"♫ {artist} - {title}")

    if genre:
        parts.append(f"🎸 Genre: {genre}")

    if description:
        parts.append(f"ℹ️ {description}")

    radio_url = get_radio_url(config)
    parts.append(f"🔗 {radio_url}")

    return " | ".join(parts)


@hook.command("stream", autohelp=False)
def stream(bot: Any, conn: Any, nick: str, message: Any) -> str:
    """- Request a temporary livestream token (15 minutes)"""
    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    nick_lower = nick.lower()
    if nick_lower in stream_token_cache:
        return "⏳ You can only request a stream token once per hour. Please wait before requesting another."

    config = bot.config.get("plugins", {}).get("radio", {})
    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        logger.error("Radio plugin not configured: missing api_url")
        return "Radio not configured. Set 'api_url' in config.json under plugins.radio"

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    try:
        response = requests.post(
            f"{api_url}/admin/livestream/token",
            headers=headers,
            json={
                "max_streaming_seconds": 900,
                "show_name": nick,
                "min_recording_duration": 5,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        token = data.get("token")
        expires_at = data.get("expires_at")

        stream_base_url = get_radio_url(config)
        stream_url = f"{stream_base_url}/stream"
        stream_url_with_token = f"{stream_url}?token={token}"

        message(f"🎬 Livestream token (valid for 15 minutes, expires: {expires_at}):", nick)
        message(f"Anonymous Stream URL: {stream_url_with_token}", nick)
        message("", nick)
        message(
            f"📝 ffmpeg example: ffmpeg -re -i input.mp3 -c:a libvorbis -b:a 128k -f ogg -method PUT '{stream_url_with_token}'",
            nick,
        )

        stream_token_cache[nick_lower] = True

        return "✅ Livestream credentials sent via private message!"

    except requests.HTTPError as e:
        if e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("detail", str(e))
                logger.error("Failed to create livestream token: %s", error_msg)
                return f"❌ Failed to create stream token: {error_msg}"
            except Exception:
                return f"❌ Failed to create stream token: HTTP {e.response.status_code}"
        return f"❌ Failed to create stream token: {e}"
    except requests.RequestException as e:
        logger.error("Stream token request failed: %s", e)
        return f"❌ Failed to create stream token: {e}"


@hook.command("queue", "request", "req")
def queue_add(text: str, event: Any, bot: Any, conn: Any, nick: str) -> str:
    """<url> - Add a song to the user queue"""
    if not text or not text.strip():
        return "Usage: .queue <url> or .request <url> - Add a YouTube/SoundCloud URL to user queue"

    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    config = bot.config.get("plugins", {}).get("radio", {})
    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        logger.error("Radio plugin not configured: missing api_url")
        return "Radio not configured. Set 'api_url' in config.json under plugins.radio"

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    url = text.strip()
    event.reply("⏳ Adding to queue...")

    try:
        response = requests.post(
            f"{api_url}/admin/queue/add",
            params={"playlist": "user"},
            headers=headers,
            files={"url": (None, url)},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()

        song_id = data.get("song_id", "Unknown")

        return f"✅ Added to user queue: {song_id}"
    except requests.HTTPError as e:
        if e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("detail", str(e))
                return f"❌ Failed to add to user queue: {error_msg}"
            except Exception:
                return f"❌ Failed to add to user queue: HTTP {e.response.status_code}"
        return f"❌ Failed to add to user queue: {e}"
    except requests.RequestException as e:
        logger.error("User queue add request failed: %s", e)
        return f"❌ Failed to add to user queue: {e}"


@hook.command("adminqueue", "adminrequest", "areq", permissions=["admins"])
def admin_queue_add(text: str, event: Any, bot: Any, conn: Any, nick: str) -> str:
    """<url> - Add a song to the fallback playlist"""
    if not text or not text.strip():
        return "Usage: .adminqueue <url> or .adminrequest <url> - Add a URL to fallback playlist"

    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    config = bot.config.get("plugins", {}).get("radio", {})
    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        logger.error("Radio plugin not configured: missing api_url")
        return "Radio not configured. Set 'api_url' in config.json under plugins.radio"

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    url = text.strip()
    event.reply("⏳ Adding to fallback playlist...")

    try:
        response = requests.post(
            f"{api_url}/admin/queue/add",
            params={"playlist": "fallback"},
            headers=headers,
            files={"url": (None, url)},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()

        song_id = data.get("song_id", "Unknown")

        return f"✅ Added to fallback playlist: {song_id}"
    except requests.HTTPError as e:
        if e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("detail", str(e))
                return f"❌ Failed to add to fallback playlist: {error_msg}"
            except Exception:
                return f"❌ Failed to add to fallback playlist: HTTP {e.response.status_code}"
        return f"❌ Failed to add to fallback playlist: {e}"
    except requests.RequestException as e:
        logger.error("Fallback playlist add request failed: %s", e)
        return f"❌ Failed to add to fallback playlist: {e}"


@hook.on_start()
def subscribe_radio_webhook() -> None:
    """Subscribe to radio webhooks on bot startup."""
    bot_instance = bot.get()
    if not bot_instance:
        logger.error("Bot instance not available for webhook subscription")
        return

    config = bot_instance.config.get("plugins", {}).get("radio", {})
    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        logger.error(
            "Radio plugin not configured: missing api_url in config.json under plugins.radio"
        )
        return

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    subscriptions = bot_instance.config.get("webhooks", {}).get("subscriptions", [])

    for sub in subscriptions:
        if sub.get("plugin") == "radio":
            try:
                response = httpx.post(
                    f"{api_url}/admin/webhooks/subscribe",
                    headers=headers,
                    json=sub,
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info("Successfully subscribed to radio webhooks")
            except httpx.HTTPError as e:
                logger.error("Failed to subscribe to radio webhooks: %s", e)

    register_webhook_handler("radio", handle_radio_webhook)
