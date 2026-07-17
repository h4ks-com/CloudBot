"""Similar songs from Last.fm's listening data.

Deezer resolves a free-text query ("midnight city m83") to a canonical artist
and track, which Last.fm turns into similar songs. Last.fm's own text search
misreads mixed title+artist input, so Deezer does the parsing and Last.fm only
does what it is good at.
"""

from pydantic import BaseModel, Field, ValidationError
from requests import RequestException

from cloudbot import hook
from cloudbot.util.web import get_session

_DEEZER_SEARCH = "https://api.deezer.com/search"
_LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
_DEFAULT_SIZE = 6


class _Artist(BaseModel):
    name: str = ""


class _DeezerTrack(BaseModel):
    title: str = ""
    artist: _Artist = Field(default_factory=_Artist)


class _DeezerSearch(BaseModel):
    data: list[_DeezerTrack] = Field(default_factory=list)


class _SimilarTrack(BaseModel):
    name: str = ""
    artist: _Artist = Field(default_factory=_Artist)
    url: str = ""


class _SimilarTracks(BaseModel):
    track: list[_SimilarTrack] = Field(default_factory=list)


class _LastfmReply(BaseModel):
    similartracks: _SimilarTracks = Field(default_factory=_SimilarTracks)


def _resolve(text: str) -> _DeezerTrack | None:
    """Deezer's top named hit for a free-text query."""
    with get_session().get(
        _DEEZER_SEARCH, params={"q": text, "limit": "1"}
    ) as r:
        r.raise_for_status()
        hits = _DeezerSearch.model_validate(r.json()).data
    named = [h for h in hits if h.title and h.artist.name]
    return named[0] if named else None


def _similar(
    api_key: str, track: _DeezerTrack, size: int
) -> list[_SimilarTrack]:
    with get_session().get(
        _LASTFM_API,
        params={
            "method": "track.getsimilar",
            "artist": track.artist.name,
            "track": track.title,
            "api_key": api_key,
            "format": "json",
            "limit": str(size),
            "autocorrect": "1",
        },
    ) as r:
        r.raise_for_status()
        reply = _LastfmReply.model_validate(r.json())
    return [t for t in reply.similartracks.track if t.name]


def _format_track(index: int, track: _SimilarTrack) -> str:
    line = f"  {index}. \x02{track.name}\x02"
    if track.artist.name:
        line += f" by {track.artist.name}"
    return f"{line} - {track.url}" if track.url else line


@hook.command("songrec")
def songrec(text, bot):
    """<song> - songs similar to <song> from Last.fm, by name or "title artist"."""
    query = text.strip()
    if not query:
        return "Usage: .songrec <song name>"
    api_key = bot.config.get_api_key("lastfm")
    if not api_key:
        return "Last.fm is not configured."

    try:
        track = _resolve(query)
        if track is None:
            return f"Couldn't find a track for '{query}'."
        similar = _similar(api_key, track, _DEFAULT_SIZE)
    except (RequestException, ValidationError) as e:
        return f"Music API error: {e}"

    label = f"{track.title} - {track.artist.name}"
    if not similar:
        return f"Last.fm has no similar tracks for '{label}'."
    header = f"Similar to \x02{label}\x02:"
    return [header] + [_format_track(i, t) for i, t in enumerate(similar, 1)]
