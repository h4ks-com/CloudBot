import logging
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import requests
from bs4 import BeautifulSoup
from cachetools import TTLCache

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util.formatting import truncate
from cloudbot.util.web import get_session
from cloudbot.webhooks.handlers import register_webhook_handler
from plugins.core.chan_track import get_users
from plugins.google_cse import last_gse_url
from plugins.youtube import last_youtube_url

logger = logging.getLogger("cloudbot")

# Rate limiting configuration
QUEUE_RATE_LIMIT_AUTHENTICATED = 20
QUEUE_RATE_LIMIT_UNAUTHENTICATED = 10
QUEUE_RATE_LIMIT_WINDOW = 3600

last_sent_messages: dict[str, dict[str, Any]] = {}
stream_token_cache = TTLCache(maxsize=1000, ttl=3600)
queue_additions_tracker: dict[tuple[str, str], list[float]] = {}


@dataclass
class SongMetadata:
    """Metadata for a song to be added to queue."""

    url: str
    reference_url: str
    title: str | None = None
    artist: str | None = None
    genre: str | None = None


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


def check_queue_rate_limit(
    conn, nick: str, chan: str
) -> tuple[bool, str | None]:
    """
    Check if user has exceeded queue addition rate limit.

    Returns:
        (allowed, error_message): allowed is True if under limit, False if over.
                                  error_message is None if allowed, otherwise contains error.
    """
    is_authenticated = check_user_authenticated(conn, nick) is None

    limit = (
        QUEUE_RATE_LIMIT_AUTHENTICATED
        if is_authenticated
        else QUEUE_RATE_LIMIT_UNAUTHENTICATED
    )
    current_time = time.time()
    cutoff_time = current_time - QUEUE_RATE_LIMIT_WINDOW

    key = (chan, nick.lower())

    if key not in queue_additions_tracker:
        queue_additions_tracker[key] = []

    recent_additions = [
        timestamp
        for timestamp in queue_additions_tracker[key]
        if timestamp > cutoff_time
    ]
    queue_additions_tracker[key] = recent_additions

    if len(recent_additions) >= limit:
        remaining_time = int(
            recent_additions[0] + QUEUE_RATE_LIMIT_WINDOW - current_time
        )
        minutes = remaining_time // 60
        seconds = remaining_time % 60

        user_type = "authenticated" if is_authenticated else "non-authenticated"
        if minutes > 0:
            time_str = f"{minutes}m {seconds}s"
        else:
            time_str = f"{seconds}s"

        return False, (
            f"⏳ Rate limit exceeded! {user_type.capitalize()} users can add {limit} songs per hour. "
            f"Try again in {time_str}. (Used: {len(recent_additions)}/{limit})"
        )

    return True, None


def record_queue_addition(nick: str, chan: str) -> None:
    """Record a successful queue addition for rate limiting."""
    key = (chan, nick.lower())
    current_time = time.time()

    if key not in queue_additions_tracker:
        queue_additions_tracker[key] = []

    queue_additions_tracker[key].append(current_time)


def get_radio_url(config: dict[str, Any]) -> str:
    """Extract the base radio URL from config (preserves port, removes paths)."""
    api_url = config.get("api_url", "")
    parsed = urlparse(api_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def convert_suno_url(url: str) -> str | None:
    """
    Convert suno.com URL to CDN format.

    Example: https://suno.com/song/ea742086-d37a-48c6-8d9d-82aea36c70ed?sh=...
    Returns: https://cdn1.suno.ai/ea742086-d37a-48c6-8d9d-82aea36c70ed.mp3
    """
    parsed = urlparse(url)

    if parsed.netloc != "suno.com":
        return None

    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) >= 2 and path_parts[0] == "song":
        song_id = path_parts[1]
        return f"https://cdn1.suno.ai/{song_id}.mp3"

    return None


def scrape_suno_metadata(url: str) -> tuple[str, str]:
    """
    Scrape suno.com page for title and artist.

    Expected format: "Song Title by ArtistName | Suno"
    Returns: (title, artist) - falls back to truncated title if parsing fails
    """
    try:
        response = get_session().get(url, timeout=10.0)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        title_tag = soup.find("title")
        if not title_tag:
            return truncate("Suno Track", 30), "Unknown Artist"

        page_title = title_tag.get_text().strip()

        if " | Suno" in page_title:
            content = page_title.replace(" | Suno", "").strip()

            if " by " in content:
                parts = content.rsplit(" by ", 1)
                song_title = parts[0].strip()
                artist = parts[1].strip()
                return song_title, artist
            else:
                return content, "Unknown Artist"

        return truncate(page_title, 30), "Unknown Artist"
    except Exception as e:
        logger.error("Failed to scrape suno.com metadata: %s", e)
        return truncate("Suno Track", 30), "Unknown Artist"


def process_song_url(url: str) -> SongMetadata:
    """
    Process a song URL and extract metadata if supported.

    Currently supports:
    - suno.com: Converts URL and scrapes metadata
    - Other URLs: Pass through as-is
    """
    parsed = urlparse(url)

    if parsed.netloc == "suno.com":
        cdn_url = convert_suno_url(url)
        if cdn_url:
            title, artist = scrape_suno_metadata(url)
            return SongMetadata(
                url=cdn_url,
                reference_url=url,
                title=title,
                artist=artist,
                genre="AI Generated by suno.com",
            )

    return SongMetadata(url=url, reference_url=url)


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
        response = requests.get(
            f"{api_url}/metadata/now", headers=headers, timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch radio metadata: %s", e)
        return None


def format_livestream_message(
    event_type: str, metadata_response: dict[str, Any], radio_url: str
) -> str | None:
    """Format livestream event with current metadata."""
    source = metadata_response.get("source", "unknown")
    metadata = metadata_response.get("metadata", {})
    title = metadata.get("title", "Unknown Track")
    artist: str | None = metadata.get("artist", None)
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
                if artist is None:
                    parts.append(f"♫ {title}")
                else:
                    parts.append(f"♫ {artist} - {title}")

            # Add radio URL
            parts.append(f"| Listen: {radio_url}")

            return " ".join(parts)
        elif source == "user":
            return f"🎬 Livestream started: ♫ {artist} - {title} | Listen: {radio_url}"
        return f"📺 Livestream started | Listen: {radio_url}"
    elif event_type == "livestream_ended":
        if source == "fallback":
            return (
                f"📻 Livestream ended, back to fallback: ♫ {artist} - {title}"
            )
        elif source == "user":
            return f"🎵 Livestream ended, now playing: ♫ {artist} - {title}"
        return "📺 Livestream ended"
    return None


def send_debounced_message(
    bot_instance: Any, chan: str, event_type: str
) -> None:
    """Fetch metadata after delay and send message if different from last."""
    config = bot_instance.config.get("plugins", {}).get("radio", {})
    connection_name = config.get("connection", "gobot")

    metadata_response = fetch_current_metadata(config)
    if not metadata_response:
        return

    radio_url = get_radio_url(config)
    message = format_livestream_message(
        event_type, metadata_response, radio_url
    )
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
    """Handle incoming webhook events from radio streaming service.

    Supported events:
    - song_changed: Track changed (user queue or fallback)
    - queue_switched: Source changed between user/fallback/livestream
    - livestream_started: Livestream began
    - livestream_ended: Livestream stopped
    - livestream_recording_done: Recording saved after livestream
    """
    config = bot_instance.config.get("plugins", {}).get("radio", {})
    channels_config = config.get("channels", {})
    connection_name = config.get("connection", "gobot")

    event_type = payload.get("event_type", "")

    if event_type in ("livestream_started", "livestream_ended"):
        for chan, chan_config in channels_config.items():
            allowed_events = chan_config.get("events", [])
            if event_type in allowed_events:
                timer = threading.Timer(
                    5.0,
                    send_debounced_message,
                    args=(bot_instance, chan, event_type),
                )
                timer.daemon = True
                timer.start()
                logger.debug(
                    "Scheduled debounced message for %s in channel %s",
                    event_type,
                    chan,
                )
        return

    message = format_radio_message(event_type, payload, config)
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
                logger.debug(
                    "Skipping duplicate immediate message for %s", chan
                )
                continue

            connection.message(chan, message)
            last_sent_messages[chan] = current_state
            logger.info("Sent immediate message to %s: %s", chan, message)


def format_radio_message(
    event_type: str, payload: dict[str, Any], config: dict[str, Any]
) -> str | None:
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
    elif event_type == "livestream_recording_done":
        data = payload.get("data", {})
        title = data.get("title") or "Untitled"
        artist = data.get("artist") or "Unknown"
        duration_seconds = data.get("duration_seconds", 0)
        recording_url = data.get("recording_url", "")

        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        duration_str = (
            f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        )

        api_url = config.get("api_url", "")
        full_url = (
            f"{api_url}{recording_url}"
            if api_url and recording_url
            else recording_url
        )

        return f"💾 Recording saved: ♫ {artist} - {title} ({duration_str}) | {full_url}"
    return None


@hook.command("rsource", autohelp=False)
def radio_source(bot: Any) -> str:
    """- Shows the source URL of the currently playing song"""
    config = bot.config.get("plugins", {}).get("radio", {})
    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        return "Radio not configured."

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    response = requests.get(
        f"{api_url}/metadata/now", headers=headers, timeout=30.0
    )
    response.raise_for_status()
    data = response.json()

    metadata = data.get("metadata", {})
    reference_url = metadata.get("reference_url")

    if not reference_url:
        return "🔗 No source URL available for the current track."

    title = metadata.get("title", "Unknown Track")
    artist = metadata.get("artist", "Unknown Artist")

    return f"🔗 Source: {artist} - {title} | {reference_url}"


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

    response = requests.get(
        f"{api_url}/metadata/now", headers=headers, timeout=30.0
    )
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
    parts.append(f" {radio_url}/radio")

    return " | ".join(parts)


@hook.command("upcoming", "next", autohelp=False)
def show_upcoming(bot: Any) -> str:
    """- Show the next 5 songs in the queue"""
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
        response = requests.get(
            f"{api_url}/queue/list",
            params={"limit": 5, "user_only": False},
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        if not data:
            return "📭 Queue is empty"

        songs = data

        lines = ["🎵 Upcoming:"]
        for i, song in enumerate(songs, 1):
            title = song.get("title", "Unknown")
            artist = song.get("artist", "Unknown Artist")
            playlist = song.get("playlist", "")

            if playlist == "user":
                emoji = "👤"
            elif playlist == "fallback":
                emoji = "📻"
            else:
                emoji = "🎶"

            lines.append(f"{i}. {emoji} {artist} - {title}")

        return " | ".join(lines)
    except requests.HTTPError as e:
        if e.response is not None:
            return f"❌ Failed to fetch queue: HTTP {e.response.status_code}"
        return f"❌ Failed to fetch queue: {e}"
    except requests.RequestException as e:
        logger.error("Queue fetch failed: %s", e)
        return f"❌ Failed to fetch queue: {e}"


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
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        token = data.get("token")
        expires_at = data.get("expires_at")

        stream_base_url = get_radio_url(config)
        stream_url = f"{stream_base_url}/stream"
        stream_url_with_token = f"{stream_url}?token={token}"

        message(
            f"🎬 Livestream token (valid for 15 minutes, expires: {expires_at}):",
            nick,
        )
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


def add_url_to_queue(
    url: str,
    playlist: str,
    config: dict[str, Any],
    event: Any,
    conn: Any,
    nick: str,
    chan: str,
) -> str:
    """
    Helper function to add a URL to a radio queue.

    Args:
        url: The URL to add
        playlist: Either "user" or "fallback"
        config: Radio plugin configuration
        event: Event object for sending status updates
        conn: IRC connection for authentication check
        nick: User's nickname
        chan: Channel name

    Returns:
        Success or error message
    """
    allowed, error_msg = check_queue_rate_limit(conn, nick, chan)
    if not allowed:
        return error_msg

    api_url = config.get("api_url")
    api_token = config.get("api_token")

    if not api_url:
        logger.error("Radio plugin not configured: missing api_url")
        return "Radio not configured. Set 'api_url' in config.json under plugins.radio"

    headers = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    queue_name = "user queue" if playlist == "user" else "fallback playlist"
    event.reply(f"⏳ Adding to {queue_name}...")

    song_metadata = process_song_url(url)

    files = {
        "url": (None, song_metadata.url),
        "reference_url": (None, song_metadata.reference_url),
    }
    if song_metadata.title:
        files["song_name"] = (None, song_metadata.title)
    if song_metadata.artist:
        files["artist"] = (None, song_metadata.artist)

    try:
        response = requests.post(
            f"{api_url}/admin/queue/add",
            params={"playlist": playlist},
            headers=headers,
            files=files,
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()

        song_id = data.get("song_id", "Unknown")

        record_queue_addition(nick, chan)

        if song_metadata.title and song_metadata.artist:
            return f"✅ Added to {queue_name}: {song_metadata.artist} - {song_metadata.title}"
        return f"✅ Added to {queue_name}: {song_id}"
    except requests.HTTPError as e:
        if e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("detail", str(e))
                return f"❌ Failed to add to {queue_name}: {error_msg}"
            except Exception:
                return f"❌ Failed to add to {queue_name}: HTTP {e.response.status_code}"
        return f"❌ Failed to add to {queue_name}: {e}"
    except requests.RequestException as e:
        logger.error("%s add request failed: %s", queue_name, e)
        return f"❌ Failed to add to {queue_name}: {e}"


@hook.command("queue", "request", "req")
def queue_add(
    text: str, event: Any, bot: Any, conn: Any, nick: str, chan: str
) -> str:
    """<url> - Add a song to the user queue"""
    if not text or not text.strip():
        return "Usage: .queue <url> or .request <url> - Add a YouTube/SoundCloud URL to user queue"

    config = bot.config.get("plugins", {}).get("radio", {})
    url = text.strip()
    return add_url_to_queue(url, "user", config, event, conn, nick, chan)


@hook.command("adminqueue", "adminrequest", "areq", permissions=["botcontrol"])
def admin_queue_add(
    text: str, event: Any, bot: Any, conn: Any, nick: str, chan: str
) -> str:
    """<url> - Add a song to the fallback playlist"""
    if not text or not text.strip():
        return "Usage: .adminqueue <url> or .adminrequest <url> - Add a URL to fallback playlist"

    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    config = bot.config.get("plugins", {}).get("radio", {})
    url = text.strip()
    return add_url_to_queue(url, "fallback", config, event, conn, nick, chan)


@hook.command("reqyt", autohelp=False)
def queue_add_youtube(
    text: str, event: Any, bot: Any, conn: Any, nick: str, chan: str
) -> str:
    """[nick] - Add your last YouTube search result (or another user's) to the user queue"""
    target_nick = text.strip() if text and text.strip() else nick

    url = last_youtube_url.get((chan, target_nick))
    if not url:
        if target_nick == nick:
            return "❌ No recent YouTube search found. Use .yt <query> first to search for a video."
        else:
            return f"❌ No recent YouTube search found for {target_nick}."

    config = bot.config.get("plugins", {}).get("radio", {})
    return add_url_to_queue(url, "user", config, event, conn, nick, chan)


@hook.command("reqgse", autohelp=False)
def queue_add_gse(
    text: str, event: Any, bot: Any, conn: Any, nick: str, chan: str
) -> str:
    """[nick] - Add your last Google search result (or another user's) to the user queue"""
    target_nick = text.strip() if text and text.strip() else nick

    url = last_gse_url.get((chan, target_nick))
    if not url:
        if target_nick == nick:
            return "❌ No recent Google search found. Use .gse <query> first to search."
        else:
            return f"❌ No recent Google search found for {target_nick}."

    config = bot.config.get("plugins", {}).get("radio", {})
    return add_url_to_queue(url, "user", config, event, conn, nick, chan)


@hook.command("areqyt", autohelp=False, permissions=["botcontrol"])
def admin_queue_add_youtube(
    text: str, event: Any, bot: Any, conn: Any, nick: str, chan: str
) -> str:
    """[nick] - Add your last YouTube search result (or another user's) to the fallback playlist"""
    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    target_nick = text.strip() if text and text.strip() else nick

    url = last_youtube_url.get((chan, target_nick))
    if not url:
        if target_nick == nick:
            return "❌ No recent YouTube search found. Use .yt <query> first to search for a video."
        else:
            return f"❌ No recent YouTube search found for {target_nick}."

    config = bot.config.get("plugins", {}).get("radio", {})
    return add_url_to_queue(url, "fallback", config, event, conn, nick, chan)


@hook.command("areqgse", autohelp=False, permissions=["botcontrol"])
def admin_queue_add_gse(
    text: str, event: Any, bot: Any, conn: Any, nick: str, chan: str
) -> str:
    """[nick] - Add your last Google search result (or another user's) to the fallback playlist"""
    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    target_nick = text.strip() if text and text.strip() else nick

    url = last_gse_url.get((chan, target_nick))
    if not url:
        if target_nick == nick:
            return "❌ No recent Google search found. Use .gse <query> first to search."
        else:
            return f"❌ No recent Google search found for {target_nick}."

    config = bot.config.get("plugins", {}).get("radio", {})
    return add_url_to_queue(url, "fallback", config, event, conn, nick, chan)


@hook.command("rsuno", "rslop", autohelp=False)
def random_slop(event: Any, bot: Any, conn: Any, nick: str, chan: str) -> str:
    """Add a random song from Suno's featured section"""
    allowed, error_msg = check_queue_rate_limit(conn, nick, chan)
    if not allowed:
        return error_msg

    config = bot.config.get("plugins", {}).get("radio", {})
    n8n_config = config.get("n8n_slop", {})

    webhook_url = n8n_config.get(
        "webhook_url", "https://n8n.t3ks.com/webhook/slopradio"
    )
    header_name = n8n_config.get("header_name")
    header_value = n8n_config.get("header_value")

    event.reply("⏳ Fetching random Suno song...")

    headers = {}
    if header_name and header_value:
        headers[header_name] = header_value

    try:
        response = requests.get(webhook_url, headers=headers, timeout=30.0)
        response.raise_for_status()

        record_queue_addition(nick, chan)

        radio_url = get_radio_url(config)

        if response.ok:
            try:
                data = response.json()
                name = data.get("name")
                artist = data.get("artist")

                if name and artist:
                    return f"✅ Added to queue: {artist} - {name} | Coming up soon at {radio_url}"
            except Exception:
                pass

        return f"✅ Added random Suno song to the queue! | Coming up soon at {radio_url}"
    except requests.HTTPError as e:
        if e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("detail", str(e))
                return f"❌ Failed to add random song: {error_msg}"
            except Exception:
                return f"❌ Failed to add random song: HTTP {e.response.status_code}"
        return f"❌ Failed to add random song: {e}"
    except requests.RequestException as e:
        logger.error("Random slop request failed: %s", e)
        return f"❌ Failed to add random song: {e}"


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

    subscriptions = bot_instance.config.get("webhooks", {}).get(
        "subscriptions", []
    )

    for sub in subscriptions:
        if sub.get("plugin") == "radio":
            try:
                response = httpx.post(
                    f"{api_url}/admin/webhooks/subscribe",
                    headers=headers,
                    json=sub,
                    timeout=30.0,
                )
                response.raise_for_status()
                logger.info("Successfully subscribed to radio webhooks")
            except httpx.HTTPError as e:
                logger.error("Failed to subscribe to radio webhooks: %s", e)

    register_webhook_handler("radio", handle_radio_webhook)
