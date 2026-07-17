"""
ReccoBeats API plugin — song recommendations based on similarity.

Uses the free ReccoBeats API (no auth required).
https://api.reccobeats.com/v1/

Workflow: search for a song → get Spotify track ID → get similar tracks.
Can also accept a Spotify track ID directly.
"""

import re

from cloudbot import hook
from cloudbot.util.web import get_session

API_BASE = "https://api.reccobeats.com/v1"

# Spotify track ID pattern (base-62, 22 chars)
SPOTIFY_ID_RE = re.compile(r"^[a-zA-Z0-9]{22}$")


def _get_spotify_track_id(query, reply):
    """Search Spotify for a track name and return its Spotify ID."""
    from cloudbot.plugins.spotify import api as spotify_api

    params = {"q": query.strip(), "offset": 0, "limit": 1, "type": "track"}
    try:
        result = spotify_api.search(params)
    except Exception:
        return None

    try:
        items = result.json()["tracks"]["items"]
    except (KeyError, ValueError):
        return None

    if not items:
        return None

    return items[0]["id"]


@hook.command("recco", "similar")
def reccobeats(text, reply):
    """<song name or Spotify track ID> - Get similar song recommendations using the ReccoBeats API.
    Accepts either a song/artist name (searches Spotify first) or a raw Spotify track ID."""
    query = text.strip()
    if not query:
        return "Usage: .recco <song name | Spotify track ID>"

    # If it looks like a Spotify track ID, use it directly
    if SPOTIFY_ID_RE.match(query):
        track_id = query
        seed_label = query
    else:
        # Search Spotify for the track to get its ID
        track_id = _get_spotify_track_id(query, reply)
        if track_id is None:
            return f"Could not find \"{query}\" on Spotify to use as a seed."
        seed_label = query

    # Call ReccoBeats recommendation endpoint
    session = get_session()
    try:
        r = session.get(
            f"{API_BASE}/track/recommendation",
            params={"seeds": track_id, "size": 5},
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
    except Exception as e:
        return f"ReccoBeats API error: {e}"

    try:
        data = r.json()
    except ValueError:
        return "ReccoBeats returned an invalid response."

    tracks = data.get("content", [])
    if not tracks:
        return f"No recommendations found for \"{seed_label}\"."

    # Format results
    lines = [f"Songs similar to \x02{seed_label}\x02:"]
    for i, track in enumerate(tracks, 1):
        title = track.get("trackTitle", "?")
        artist = track.get("artistName", "?")
        playcount = track.get("playcount")
        popularity = ""
        if playcount is not None:
            popularity = f" ({playcount:,} plays)"

        spotify_url = ""
        links = track.get("links", {})
        spotify_data = links.get("spotify", {})
        if isinstance(spotify_data, dict):
            spotify_url = spotify_data.get("trackExternalUrl", "")

        line = f"{i}. \x02{title}\x02 by \x02{artist}\x02{popularity}"
        if spotify_url:
            line += f" — {spotify_url}"
        lines.append(line)

    return "\n".join(lines)
