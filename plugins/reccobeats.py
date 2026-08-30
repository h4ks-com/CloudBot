"""
ReccoBeats plugin - Free music recommendation API
https://reccobeats.com
"""
from cloudbot import hook
from cloudbot.util.web import get_session

RECCO_URL = "https://api.reccobeats.com/v1/track/recommendation"
DEFAULT_SIZE = 5


def _get_spotify_track_id(query, reply):
    """Use the spotify plugin's search to resolve a song name to a Spotify track ID."""
    from plugins.spotify import _search

    try:
        data = _search(query, "track", reply)
    except Exception:
        return None

    if data is None:
        return None

    return data.get("id")


@hook.command("recco", "similar")
def recco(text, reply):
    """<song name or Spotify track ID> - Get similar song recommendations via ReccoBeats"""
    query = text.strip()
    if not query:
        return "Usage: .recco <song name>"

    # If the input looks like a Spotify track ID (22 char alphanumeric), use it directly
    # otherwise resolve via spotify search
    if len(query) == 22 and query.isalnum():
        track_id = query
        seed_label = f"Spotify ID {track_id}"
    else:
        track_id = _get_spotify_track_id(query, reply)
        if track_id is None:
            return f"Could not find a Spotify track for '{query}' - try .spotify to search."
        seed_label = query

    # Query ReccoBeats
    try:
        with get_session().get(
            RECCO_URL,
            params={"seeds": track_id, "size": DEFAULT_SIZE},
        ) as r:
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ReccoBeats API error: {e}"

    tracks = data.get("content", [])
    if not tracks:
        return f"No recommendations found for '{seed_label}'."

    lines = [f"Similar to \x02{seed_label}\x02 - {len(tracks)} recommendations:"]
    for i, track in enumerate(tracks, 1):
        title = track.get("trackTitle", "Unknown")
        artist = track.get("mainArtists", "Unknown")
        if isinstance(artist, list):
            artist = ", ".join(
                a.get("artistName", str(a)) if isinstance(a, dict) else str(a)
                for a in artist
            )
        link = ""
        for off in track.get("offering", []):
            url = off.get("url")
            if url:
                link = url
                break
        line = f"  {i}. \x02{title}\x02 by \x02{artist}\x02"
        if link:
            line += f" - {link}"
        lines.append(line)

    return "\n".join(lines)
