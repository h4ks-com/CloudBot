from collections.abc import Callable

import pytest
from responses import RequestsMock

from plugins import lyricsnmusic as plugin
from plugins.lyricsnmusic import Song

SONGS = [Song(artist=f"Artist {i}", title=f"Song {i}") for i in range(5)]

GENIUS_PAGE = """
<html><body>
  <div data-lyrics-container="true">
    <div class="LyricsHeader__Container-sc-1">284 Contributors Translations Deutsch</div>
    [Verse 1]<br/>first line of the song<br/>second line of the song
  </div>
  <div data-lyrics-container="true"></div>
  <div data-lyrics-container="true">[Chorus]<br/>the hook goes here</div>
</body></html>
"""


@pytest.fixture(autouse=True)
def clean_state() -> None:
    plugin.pending.clear()
    plugin.listed.clear()
    plugin._scrape_genius.cache_clear()
    plugin._lyrics_ovh.cache_clear()


def song_hit(artist: str, title: str, url: str, snippet: str = "") -> dict:
    hit: dict = {
        "result": {
            "_type": "song",
            "title": title,
            "url": url,
            "primary_artist": {"name": artist},
        }
    }
    if snippet:
        hit["highlights"] = [{"property": "lyrics", "value": snippet}]
    return hit


def add_search(mock_requests: RequestsMock, *hits: dict) -> None:
    mock_requests.add(
        "GET",
        plugin.GENIUS_SEARCH_API,
        json={"response": {"sections": [{"type": "song", "hits": list(hits)}]}},
    )


def collect() -> tuple[list[str], Callable[..., None]]:
    sent: list[str] = []
    return sent, lambda *lines: sent.extend(lines)


def test_show_page_hands_out_one_page_at_a_time():
    plugin.pending["#chan"]["matt"] = SONGS
    sent, reply = collect()

    assert plugin.show_page("#chan", "matt", "matt", reply) is None
    assert [line[:2] for line in sent[:3]] == ["1)", "2)", "3)"]
    assert sent[3].startswith("2 more")
    assert list(plugin.listed["#chan"]["matt"]) == SONGS[:3]


def test_show_page_reports_a_drained_queue():
    sent, reply = collect()
    assert plugin.show_page("#chan", "matt", "matt", reply) == (
        "No [more] results found."
    )
    assert sent == []


def test_take_page_stops_when_the_queue_empties_underneath_it():
    plugin.pending["#chan"]["matt"] = SONGS[:2]
    queue = plugin.pending["#chan"]["matt"]
    assert len(plugin.take_page(queue)) == 2
    assert plugin.take_page(queue) == []


def test_lyricsn_rejects_an_unknown_nick():
    _, reply = collect()
    assert (
        plugin.lyricsn("bob", "#chan", "matt", reply)
        == "Nick 'bob' has no queue."
    )


def test_getlyrics_needs_a_listing_first():
    assert "Nothing listed" in plugin.getlyrics("1", "#chan", "matt")


@pytest.mark.parametrize("choice", ["0", "4", "abc", "²", "-1", ""])
def test_getlyrics_rejects_bad_choices(choice: str):
    plugin.listed["#chan"]["matt"] = SONGS[:3]
    assert plugin.getlyrics(choice, "#chan", "matt") == (
        "Pick a number between 1 and 3."
    )


def test_getlyrics_pastes_the_full_words(monkeypatch: pytest.MonkeyPatch):
    plugin.listed["#chan"]["matt"] = SONGS[:3]
    monkeypatch.setattr(
        plugin, "fetch_lyrics", lambda song: "one\n\ntwo\nthree"
    )
    monkeypatch.setattr(plugin.web, "paste", lambda data, ext: "http://paste/x")

    out = plugin.getlyrics("2", "#chan", "matt")
    assert out == "Artist 1 - Song 1: one / two / three... full: http://paste/x"


def test_getlyrics_reports_a_song_with_no_lyrics(
    monkeypatch: pytest.MonkeyPatch,
):
    plugin.listed["#chan"]["matt"] = SONGS[:3]
    monkeypatch.setattr(plugin, "fetch_lyrics", lambda song: "")
    assert plugin.getlyrics("1", "#chan", "matt") == (
        "No lyrics available for Artist 0 - Song 0."
    )


def test_search_returns_songs_with_snippets(mock_requests: RequestsMock):
    add_search(
        mock_requests,
        song_hit(
            "Radiohead",
            "Creep",
            "https://genius.com/Radiohead-creep-lyrics",
            "first line of the song\nsecond line of the song",
        ),
    )
    songs = plugin.search("radiohead creep")
    assert len(songs) == 1
    assert songs[0].label == "Radiohead - Creep"
    assert (
        songs[0].snippet == "first line of the song / second line of the song"
    )


def test_search_drops_non_lyric_pages_and_duplicates(
    mock_requests: RequestsMock,
):
    add_search(
        mock_requests,
        song_hit(
            "Sabrina Carpenter",
            "Espresso",
            "https://genius.com/Sabrina-carpenter-espresso-lyrics",
        ),
        song_hit(
            "Sabrina Carpenter",
            "Espresso",
            "https://genius.com/Sabrina-carpenter-espresso-lyrics",
        ),
        song_hit(
            "LIPA MAX",
            "My Most Scrobbled Songs",
            "https://genius.com/Lipamax-scrobbled-annotated",
        ),
        {"result": {"_type": "artist", "name": "Sabrina Carpenter"}},
    )
    assert [song.label for song in plugin.search("espresso")] == [
        "Sabrina Carpenter - Espresso"
    ]


def test_search_skips_empty_query():
    assert plugin.search("   ") == []


def test_search_raises_on_upstream_failure(mock_requests: RequestsMock):
    mock_requests.add("GET", plugin.GENIUS_SEARCH_API, status=503)
    with pytest.raises(plugin.LyricsError):
        plugin.search("anything")


def test_extract_genius_lyrics_drops_page_chrome():
    text = plugin.extract_genius_lyrics(GENIUS_PAGE)
    assert "Contributors" not in text
    assert text.startswith("[Verse 1]\nfirst line of the song")
    assert text.endswith("[Chorus]\nthe hook goes here")


def test_fetch_lyrics_scrapes_the_matched_page(mock_requests: RequestsMock):
    url = "https://genius.com/Radiohead-creep-lyrics"
    mock_requests.add("GET", url, body=GENIUS_PAGE)
    song = Song(artist="Radiohead", title="Creep", url=url)
    assert "the hook goes here" in plugin.fetch_lyrics(song)


def test_fetch_lyrics_falls_back_to_lyrics_ovh(mock_requests: RequestsMock):
    mock_requests.add(
        "GET",
        f"{plugin.LYRICS_OVH_API}/Radiohead/Creep",
        json={"lyrics": "the hook goes here"},
    )
    assert (
        plugin.fetch_lyrics(Song(artist="Radiohead", title="Creep"))
        == "the hook goes here"
    )


def test_fetch_lyrics_returns_empty_when_nothing_has_the_song(
    mock_requests: RequestsMock,
):
    mock_requests.add(
        "GET", f"{plugin.LYRICS_OVH_API}/Nobody/Nothing", status=404
    )
    assert plugin.fetch_lyrics(Song(artist="Nobody", title="Nothing")) == ""


def test_lyrics_command_lists_matches(mock_requests: RequestsMock):
    add_search(
        mock_requests,
        song_hit(
            "Queen", "Bohemian Rhapsody", "https://genius.com/q-br-lyrics"
        ),
        song_hit(
            "Panic! at the Disco",
            "Bohemian Rhapsody",
            "https://genius.com/p-br-lyrics",
        ),
    )
    sent, reply = collect()

    assert plugin.lyrics("bohemian rhapsody", "#chan", "matt", reply) is None
    assert sent == [
        "1) Queen - Bohemian Rhapsody - https://genius.com/q-br-lyrics",
        "2) Panic! at the Disco - Bohemian Rhapsody - https://genius.com/p-br-lyrics",
    ]
    assert list(plugin.listed["#chan"]["matt"]) == [
        Song("Queen", "Bohemian Rhapsody", "https://genius.com/q-br-lyrics"),
        Song(
            "Panic! at the Disco",
            "Bohemian Rhapsody",
            "https://genius.com/p-br-lyrics",
        ),
    ]


def test_lyrics_command_reports_no_match(mock_requests: RequestsMock):
    add_search(mock_requests)
    _, reply = collect()
    assert (
        plugin.lyrics("zzzz", "#chan", "matt", reply)
        == "Nothing found for 'zzzz'."
    )


def test_lyrics_command_reports_a_dead_upstream(mock_requests: RequestsMock):
    mock_requests.add("GET", plugin.GENIUS_SEARCH_API, status=503)
    _, reply = collect()
    assert plugin.lyrics("anything", "#chan", "matt", reply).startswith(
        "Lyrics service error:"
    )


def test_getlyrics_reports_a_dead_upstream(mock_requests: RequestsMock):
    plugin.listed["#chan"]["matt"] = [
        Song("Radiohead", "Creep", "https://genius.com/rc-lyrics")
    ]
    mock_requests.add("GET", "https://genius.com/rc-lyrics", status=503)
    assert plugin.getlyrics("1", "#chan", "matt").startswith(
        "Lyrics service error:"
    )
