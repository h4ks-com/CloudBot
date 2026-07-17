"""ReccoBeats free music recommendations (https://reccobeats.com), keyless.

ReccoBeats seeds off its OWN track ids and has no name search, so a name is
resolved through Deezer (also keyless) to an ISRC, which ReccoBeats can map to
its id. A pasted Spotify track id/url is fed to the same mapping directly.
"""

import re
from typing import NamedTuple

from requests import RequestException

from cloudbot import hook
from cloudbot.util.web import get_session

_DEEZER_SEARCH = "https://api.deezer.com/search"
_DEEZER_TRACK = "https://api.deezer.com/track"
_RECCO_TRACK = "https://api.reccobeats.com/v1/track"
_RECCO_RECOMMEND = "https://api.reccobeats.com/v1/track/recommendation"
_DEFAULT_SIZE = 5
# A Spotify track id is 22 base62 chars; accept it bare or lifted from a
# spotify:track:<id> uri or an open.spotify.com/track/<id> url.
_SPOTIFY_URL_RE = re.compile(r"track[:/]([a-zA-Z0-9]{22})")
_SPOTIFY_BARE_RE = re.compile(r"[a-zA-Z0-9]{22}")


# The two APIs' JSON is the dynamic boundary; these narrow it before it is
# parsed into the records below.
JsonObject = dict[str, object]


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_object(value: object) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _as_array(value: object) -> list[object]:
    return value if isinstance(value, list) else []


class Seed(NamedTuple):
    """An id to seed from, paired with the resolved track label for the reply."""

    external_id: str
    label: str


class Track(NamedTuple):
    title: str
    artists: str
    link: str


def _parse_track(raw: object) -> Track:
    obj = _as_object(raw)
    artists = ", ".join(
        name
        for a in _as_array(obj.get("artists"))
        if (name := _as_str(_as_object(a).get("name")))
    )
    return Track(
        title=_as_str(obj.get("trackTitle")) or "Unknown",
        artists=artists,
        link=_as_str(obj.get("href")),
    )


def _isrc_seed(name: str) -> Seed | None:
    """Resolve a name to an ISRC via Deezer, labelled with what it matched."""
    with get_session().get(
        _DEEZER_SEARCH, params={"q": name, "limit": "1"}
    ) as r:
        r.raise_for_status()
        hits = _as_array(r.json().get("data"))
    if not hits:
        return None
    hit = _as_object(hits[0])
    track_id = hit.get("id")
    if not isinstance(track_id, int):
        return None
    with get_session().get(f"{_DEEZER_TRACK}/{track_id}") as r:
        r.raise_for_status()
        isrc = _as_str(r.json().get("isrc"))
    if not isrc:
        return None
    artist = _as_str(_as_object(hit.get("artist")).get("name"))
    return Seed(isrc, f"{_as_str(hit.get('title'))} - {artist}")


def _seed(query: str) -> Seed | None:
    """An id ReccoBeats can map — a pasted Spotify id, or a name via Deezer."""
    url_match = _SPOTIFY_URL_RE.search(query)
    if url_match:
        return Seed(url_match.group(1), query)
    if _SPOTIFY_BARE_RE.fullmatch(query):
        return Seed(query, query)
    return _isrc_seed(query)


def _recco_id(external_id: str) -> str | None:
    """ReccoBeats' own id for a Spotify id or ISRC, which is what seeds a run."""
    with get_session().get(_RECCO_TRACK, params={"ids": external_id}) as r:
        r.raise_for_status()
        content = _as_array(r.json().get("content"))
    return _as_str(_as_object(content[0]).get("id")) if content else None


def _recommendations(recco_id: str, size: int) -> list[Track]:
    with get_session().get(
        _RECCO_RECOMMEND, params={"seeds": recco_id, "size": str(size)}
    ) as r:
        r.raise_for_status()
        return [_parse_track(t) for t in _as_array(r.json().get("content"))]


def _format_track(index: int, track: Track) -> str:
    line = f"  {index}. \x02{track.title}\x02"
    if track.artists:
        line += f" by {track.artists}"
    return f"{line} - {track.link}" if track.link else line


@hook.command("recco", "similar")
def recco(text):
    """<song name or Spotify track> - similar songs from ReccoBeats."""
    query = text.strip()
    if not query:
        return "Usage: .recco <song name>"

    try:
        seed = _seed(query)
        if seed is None:
            return f"Couldn't find a track for '{query}'."
        recco_id = _recco_id(seed.external_id)
        if not recco_id:
            return f"ReccoBeats doesn't know '{seed.label}' yet, so it can't seed from it."
        tracks = _recommendations(recco_id, _DEFAULT_SIZE)
    except RequestException as e:
        return f"Music API error: {e}"

    if not tracks:
        return f"No recommendations for '{seed.label}'."
    header = f"Similar to \x02{seed.label}\x02:"
    return [header] + [_format_track(i, t) for i, t in enumerate(tracks, 1)]
