import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import googlemaps.exceptions
import pytest
import pytz
import responses

from plugins import gmaps
from plugins.locate import GeolocationException, GoogleLocation
from plugins.ratelimit import ratelimit_table
from plugins.ratelimit import record as rl_record


@pytest.fixture()
def db(mock_db):
    ratelimit_table.create(mock_db.engine)
    return mock_db.session()


@pytest.fixture(autouse=True)
def _clean_guess_games():
    gmaps.guess_games.clear()
    yield
    gmaps.guess_games.clear()


def make_bot(api_key="AIzaTestKey1234567890"):
    bot = MagicMock()
    bot.config = {"api_keys": {"google": api_key}} if api_key else {}
    return bot


def exceed_hourly_limit(db):
    for _ in range(gmaps.MAX_HOURLY_REQUESTS):
        rl_record(db, gmaps.GMAPS_BUCKET)


def collecting_reply():
    replies = []
    return replies, (lambda *a: replies.extend(a))


def make_location(**overrides):
    data = {
        "lat": 10.0,
        "lng": 20.0,
        "url": "https://maps.example/place",
        "tags": "locality",
        "location_name": "Some Place",
        "country": "Wonderland",
    }
    data.update(overrides)
    return GoogleLocation(**data)


# --- ratelimit ---


def test_ratelimit_none_when_under_limit(db):
    assert gmaps.ratelimit(db) is None


def test_ratelimit_message_when_over_hourly_limit(db):
    exceed_hourly_limit(db)
    assert (
        gmaps.ratelimit(db) == "Too many gmaps requests this hour. Try later."
    )


# --- directions ---


def test_directions_rate_limited(db):
    exceed_hourly_limit(db)
    replies, reply = collecting_reply()
    result = gmaps.directions(
        "A to B", None, reply, make_bot(), "nick", "#chan", db
    )
    assert result == "Too many gmaps requests this hour. Try later."
    assert replies == []


def test_directions_no_api_key(db):
    replies, reply = collecting_reply()
    result = gmaps.directions(
        "A to B", None, reply, make_bot(api_key=None), "nick", "#chan", db
    )
    assert result == "No or wrong Google API key configured."


def test_directions_empty_text(db):
    replies, reply = collecting_reply()
    result = gmaps.directions(
        "  ", None, reply, make_bot(), "nick", "#chan", db
    )
    assert result == "Usage: gd [mode] <origin> to <destination>"


def test_directions_bad_quoted_args(db):
    replies, reply = collecting_reply()
    result = gmaps.directions(
        '"A" "B" "C" "D" "E"', None, reply, make_bot(), "nick", "#chan", db
    )
    assert result == "Usage: gd [mode] <origin> to <destination>"


def test_directions_quoted_unknown_mode(db):
    replies, reply = collecting_reply()
    result = gmaps.directions(
        'teleport "A" to "B"', None, reply, make_bot(), "nick", "#chan", db
    )
    assert result == "Unknown mode: 'teleport'. Possible modes: " + gmaps.modes


def test_directions_points_length_invalid(db):
    replies, reply = collecting_reply()
    result = gmaps.directions(
        "nowhere", None, reply, make_bot(), "nick", "#chan", db
    )
    assert result == "Usage: gd [mode] <origin> to <destination>"


def _route(steps):
    return {
        "legs": [
            {
                "duration": {"text": "10 mins"},
                "distance": {"text": "5 km"},
                "start_address": "Origin",
                "end_address": "Destination",
                "steps": steps,
            }
        ]
    }


def _walk_step(text="Walk north"):
    return {
        "travel_mode": "WALKING",
        "distance": {"text": "100 m"},
        "html_instructions": text,
    }


def _transit_step():
    return {
        "travel_mode": "TRANSIT",
        "distance": {"text": "2 km"},
        "html_instructions": "Take the bus",
        "transit_details": {
            "arrival_time": {"text": "10:00am"},
            "headsign": "Downtown",
            "num_stops": 3,
        },
    }


def test_directions_api_error(db):
    replies, reply = collecting_reply()
    with patch("plugins.gmaps.googlemaps.Client") as mock_client_cls:
        mock_client_cls.return_value.directions.side_effect = (
            googlemaps.exceptions.ApiError("MAX_ROUTE_LENGTH_EXCEEDED")
        )
        result = gmaps.directions(
            "driving A to B", None, reply, make_bot(), "nick", "#chan", db
        )
    assert result == "Error: MAX_ROUTE_LENGTH_EXCEEDED"


def test_directions_no_results(db):
    replies, reply = collecting_reply()
    with patch("plugins.gmaps.googlemaps.Client") as mock_client_cls:
        mock_client_cls.return_value.directions.return_value = []
        result = gmaps.directions(
            "driving A to B", None, reply, make_bot(), "nick", "#chan", db
        )
    assert result == "No results found."


def test_directions_success_with_transit_step(db):
    replies, reply = collecting_reply()
    with patch("plugins.gmaps.googlemaps.Client") as mock_client_cls:
        mock_client_cls.return_value.directions.return_value = [
            _route([_walk_step(), _transit_step()])
        ]
        result = gmaps.directions(
            "driving A to B", None, reply, make_bot(), "nick", "#chan", db
        )
    assert result is None
    assert any(
        "Searching directions from 'A' to 'B'" in line for line in replies
    )
    assert any("Origin" in line and "Destination" in line for line in replies)
    assert any(
        "Downtown" in line and "10:00am" in line and "3 stops" in line
        for line in replies
    )


def test_directions_success_quoted_len3_no_mode(db):
    replies, reply = collecting_reply()
    with patch("plugins.gmaps.googlemaps.Client") as mock_client_cls:
        mock_client_cls.return_value.directions.return_value = [
            _route([_walk_step()])
        ]
        result = gmaps.directions(
            '"New York" to "Boston"',
            None,
            reply,
            make_bot(),
            "nick",
            "#chan",
            db,
        )
    assert result is None
    assert any("using 'all'" in line for line in replies)


def test_directions_success_unquoted_no_mode(db):
    replies, reply = collecting_reply()
    with patch("plugins.gmaps.googlemaps.Client") as mock_client_cls:
        mock_client_cls.return_value.directions.return_value = [
            _route([_walk_step()])
        ]
        result = gmaps.directions(
            "A to B", None, reply, make_bot(), "nick", "#chan", db
        )
    assert result is None
    call_kwargs = mock_client_cls.return_value.directions.call_args.kwargs
    assert call_kwargs["mode"] == "transit"


def test_directions_truncates_steps_in_channel(db):
    replies, reply = collecting_reply()
    steps = [_walk_step(f"Step {i}") for i in range(15)]
    with patch("plugins.gmaps.googlemaps.Client") as mock_client_cls:
        mock_client_cls.return_value.directions.return_value = [_route(steps)]
        result = gmaps.directions(
            "driving A to B", None, reply, make_bot(), "nick", "#chan", db
        )
    assert result is None
    assert any("More steps" in line for line in replies)
    step_lines = [line for line in replies if line.startswith("    ")]
    assert len(step_lines) == gmaps.MAX_OUTPUT_LINES


def test_directions_no_truncation_in_private_message(db):
    replies, reply = collecting_reply()
    steps = [_walk_step(f"Step {i}") for i in range(15)]
    with patch("plugins.gmaps.googlemaps.Client") as mock_client_cls:
        mock_client_cls.return_value.directions.return_value = [_route(steps)]
        result = gmaps.directions(
            "driving A to B", None, reply, make_bot(), "nick", "bob", db
        )
    assert result is None
    assert not any("More steps" in line for line in replies)
    step_lines = [line for line in replies if line.startswith("    ")]
    assert len(step_lines) == 15


# --- upload_image ---


def test_upload_image_uses_file_wrapper():
    image = MagicMock()
    with patch(
        "plugins.gmaps.FileIrcResponseWrapper.upload_file",
        return_value="http://img/1",
    ) as mock_upload:
        result = gmaps.upload_image(image)
    assert result == "http://img/1"
    image.save.assert_called_once()
    assert mock_upload.call_args.args[1] == "st"


# --- streetview (sv) ---


def test_sv_empty_text(db):
    replies, reply = collecting_reply()
    assert (
        gmaps.streetview("  ", reply, make_bot(), db) == "Usage: sv <location>"
    )


def test_sv_no_api_key(db):
    replies, reply = collecting_reply()
    result = gmaps.streetview("Place", reply, make_bot(api_key=None), db)
    assert result == "This command requires a Google API key."


def test_sv_rate_limited(db):
    exceed_hourly_limit(db)
    replies, reply = collecting_reply()
    result = gmaps.streetview("Place", reply, make_bot(), db)
    assert result == "Too many gmaps requests this hour. Try later."


def test_sv_invalid_parameter(db):
    replies, reply = collecting_reply()
    result = gmaps.streetview("Place foo:1", reply, make_bot(), db)
    assert result == "Invalid parameter: foo"


def test_sv_invalid_value_for_known_param(db):
    replies, reply = collecting_reply()
    result = gmaps.streetview("Place fov:12.5", reply, make_bot(), db)
    assert (
        result == "Invalid value for parameter 'fov'. Must be a integer number."
    )


def test_sv_lat_lng_direct(db):
    replies, reply = collecting_reply()
    location = make_location(location_name="Nowhereville")
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_lat_lng", return_value=location
        ),
        patch(
            "plugins.gmaps.search_panoramas",
            return_value=[MagicMock(pano_id="p1")],
        ),
        patch("plugins.gmaps.get_streetview", return_value=MagicMock()),
        patch("plugins.gmaps.upload_image", return_value="http://img/1"),
    ):
        result = gmaps.streetview("12.34, -56.78", reply, make_bot(), db)
    assert result == "📸 Nowhereville - http://img/1"


def test_sv_lat_lng_geolocation_exception_falls_back_to_coords(db):
    replies, reply = collecting_reply()
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_lat_lng",
            side_effect=GeolocationException("bad"),
        ),
        patch(
            "plugins.gmaps.search_panoramas",
            return_value=[MagicMock(pano_id="p1")],
        ),
        patch("plugins.gmaps.get_streetview", return_value=MagicMock()),
        patch("plugins.gmaps.upload_image", return_value="http://img/1"),
    ):
        result = gmaps.streetview("12.34, -56.78", reply, make_bot(), db)
    assert result == "📸 12.34, -56.78 - http://img/1"


def test_sv_address_input(db):
    replies, reply = collecting_reply()
    location = make_location(location_name="Some Address Place")
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_address", return_value=location
        ),
        patch(
            "plugins.gmaps.search_panoramas",
            return_value=[MagicMock(pano_id="p1")],
        ),
        patch("plugins.gmaps.get_streetview", return_value=MagicMock()),
        patch("plugins.gmaps.upload_image", return_value="http://img/2"),
    ):
        result = gmaps.streetview("Eiffel Tower", reply, make_bot(), db)
    assert result == "📸 Some Address Place - http://img/2"


def test_sv_address_geolocation_exception_returns_message(db):
    replies, reply = collecting_reply()
    with patch(
        "plugins.gmaps.GoogleLocation.from_address",
        side_effect=GeolocationException("No results found."),
    ):
        result = gmaps.streetview("Nowhere at all", reply, make_bot(), db)
    assert result == "No results found."


def test_sv_no_panoramas_found(db):
    replies, reply = collecting_reply()
    location = make_location()
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_address", return_value=location
        ),
        patch("plugins.gmaps.search_panoramas", return_value=[]),
    ):
        result = gmaps.streetview("Somewhere", reply, make_bot(), db)
    assert result == "No panoramas found for this location."


def test_sv_move_shifts_coordinates(db):
    from pyproj import Geod

    replies, reply = collecting_reply()
    location = make_location(lat=10.0, lng=20.0, location_name="Place")
    geod = Geod(ellps="WGS84")
    expected_lng, expected_lat, _ = geod.fwd(20.0, 10.0, 90.0, 100.0)
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_address", return_value=location
        ),
        patch("plugins.gmaps.search_panoramas") as mock_search,
        patch("plugins.gmaps.get_streetview", return_value=MagicMock()),
        patch("plugins.gmaps.upload_image", return_value="http://img/1"),
    ):
        mock_search.return_value = [MagicMock(pano_id="p1")]
        result = gmaps.streetview(
            "Place move:100 move_heading:90", reply, make_bot(), db
        )
    kwargs = mock_search.call_args.kwargs
    assert kwargs["lat"] == pytest.approx(expected_lat)
    assert kwargs["lon"] == pytest.approx(expected_lng)
    assert result == "📸 Place - http://img/1"


def test_sv_success_records_ratelimit_usage(db):
    replies, reply = collecting_reply()
    location = make_location()
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_address", return_value=location
        ),
        patch(
            "plugins.gmaps.search_panoramas",
            return_value=[MagicMock(pano_id="p1")],
        ),
        patch("plugins.gmaps.get_streetview", return_value=MagicMock()),
        patch("plugins.gmaps.upload_image", return_value="http://img/1"),
    ):
        gmaps.streetview("Somewhere", reply, make_bot(), db)
    rows = db.execute(ratelimit_table.select()).fetchall()
    assert len(rows) == 1
    assert rows[0].bucket == gmaps.GMAPS_BUCKET


# --- geo_guess / new_guess_game ---


def test_geo_guess_reveal_no_active_game():
    replies, reply = collecting_reply()
    result = gmaps.geo_guess(
        "reveal", "#chan", "alice", reply, make_bot(), MagicMock()
    )
    assert (
        result
        == "There is no active GeoGuess game in this channel. Start one with '.geoguess'."
    )


def test_geo_guess_reveal_active_game(freeze_time):
    location = make_location(location_name="Secret City", country="Secretland")
    gmaps.guess_games["#chan"] = gmaps.GuessGame(
        location, datetime.now(pytz.timezone("UTC")), "http://img/1"
    )
    replies, reply = collecting_reply()
    result = gmaps.geo_guess(
        "reveal", "#chan", "alice", reply, make_bot(), MagicMock()
    )
    assert "Secret City" in result
    assert "#chan" not in gmaps.guess_games


def test_geo_guess_no_active_game_for_guess():
    replies, reply = collecting_reply()
    result = gmaps.geo_guess(
        "France", "#chan", "alice", reply, make_bot(), MagicMock()
    )
    assert (
        result
        == "There is no active GeoGuess game in this channel. Start one with '.geoguess'."
    )


def test_geo_guess_correct_guess_ends_game(freeze_time):
    location = make_location(country="France", location_name="Paris")
    gmaps.guess_games["#chan"] = gmaps.GuessGame(
        location, datetime.now(pytz.timezone("UTC")), "http://img/1"
    )
    replies, reply = collecting_reply()
    result = gmaps.geo_guess(
        "france", "#chan", "alice", reply, make_bot(), MagicMock()
    )
    assert "alice guessed the country correctly!" in result
    assert "Paris" in result
    assert "#chan" not in gmaps.guess_games


def test_geo_guess_incorrect_guess_keeps_game(freeze_time):
    location = make_location(country="France", location_name="Paris")
    gmaps.guess_games["#chan"] = gmaps.GuessGame(
        location, datetime.now(pytz.timezone("UTC")), "http://img/1"
    )
    replies, reply = collecting_reply()
    result = gmaps.geo_guess(
        "Antarctica", "#chan", "alice", reply, make_bot(), MagicMock()
    )
    assert result == "🔍 Incorrect guess. Try again!"
    assert "#chan" in gmaps.guess_games


def test_geo_guess_already_active_game(freeze_time):
    location = make_location()
    gmaps.guess_games["#chan"] = gmaps.GuessGame(
        location, datetime.now(pytz.timezone("UTC")), "http://img/1"
    )
    replies, reply = collecting_reply()
    result = gmaps.geo_guess(
        "", "#chan", "alice", reply, make_bot(), MagicMock()
    )
    assert "already an active GeoGuess game" in result
    assert "http://img/1" in result


def test_geo_guess_expired_game_starts_new(freeze_time):
    location = make_location()
    old_start = datetime.now(pytz.timezone("UTC")) - timedelta(seconds=120)
    gmaps.guess_games["#chan"] = gmaps.GuessGame(
        location, old_start, "http://img/1"
    )
    replies, reply = collecting_reply()
    with patch("plugins.gmaps.new_guess_game", return_value="STUB") as mock_new:
        result = gmaps.geo_guess(
            "", "#chan", "alice", reply, make_bot(), MagicMock()
        )
    mock_new.assert_called_once()
    assert result == "STUB"
    assert any("Starting a new GeoGuess game" in line for line in replies)


def test_geo_guess_starts_new_game_when_none_active():
    replies, reply = collecting_reply()
    with patch("plugins.gmaps.new_guess_game", return_value="STUB") as mock_new:
        result = gmaps.geo_guess(
            "", "#chan", "alice", reply, make_bot(), MagicMock()
        )
    mock_new.assert_called_once()
    assert result == "STUB"


RANDOMLAND_RE = re.compile(r"https://api\.3geonames\.org/randomland\.\w+\.json")


def test_new_guess_game_no_api_key(db):
    assert (
        gmaps.new_guess_game(make_bot(api_key=None), "#chan", db)
        == "This command requires a Google API key."
    )


def test_new_guess_game_rate_limited(db):
    exceed_hourly_limit(db)
    assert (
        gmaps.new_guess_game(make_bot(), "#chan", db)
        == "Too many gmaps requests this hour. Try later."
    )


def test_new_guess_game_success(db, mock_requests):
    mock_requests.add(
        responses.GET,
        RANDOMLAND_RE,
        json={"nearest": {"latt": "48.85", "longt": "2.35"}},
    )
    location = make_location(country=None, location_name="Paris")
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_lat_lng", return_value=location
        ),
        patch(
            "plugins.gmaps.search_panoramas",
            return_value=[MagicMock(pano_id="p1")],
        ),
        patch("plugins.gmaps.get_streetview", return_value=MagicMock()),
        patch("plugins.gmaps.upload_image", return_value="http://img/9"),
    ):
        result = gmaps.new_guess_game(make_bot(), "#chan", db)
    assert result == "🌎 Try to guess what country is: http://img/9"
    assert "#chan" in gmaps.guess_games
    assert gmaps.guess_games["#chan"].image_url == "http://img/9"
    assert gmaps.guess_games["#chan"].location.country


def test_new_guess_game_no_panoramas_exhausts_attempts(db, mock_requests):
    mock_requests.add(
        responses.GET,
        RANDOMLAND_RE,
        json={"nearest": {"latt": "48.85", "longt": "2.35"}},
    )
    location = make_location()
    with (
        patch(
            "plugins.gmaps.GoogleLocation.from_lat_lng", return_value=location
        ),
        patch("plugins.gmaps.search_panoramas", return_value=[]),
    ):
        result = gmaps.new_guess_game(make_bot(), "#chan", db)
    assert result == "Could not find a location with a street view image."
    assert "#chan" not in gmaps.guess_games


def test_new_guess_game_geolocation_failures_exhaust_attempts(
    db, mock_requests
):
    mock_requests.add(
        responses.GET,
        RANDOMLAND_RE,
        json={"nearest": {"latt": "48.85", "longt": "2.35"}},
    )
    with patch(
        "plugins.gmaps.GoogleLocation.from_lat_lng",
        side_effect=GeolocationException("nope"),
    ):
        result = gmaps.new_guess_game(make_bot(), "#chan", db)
    assert result == "Could not find a location with a street view image."


def test_new_guess_game_bad_json_response_retries(db, mock_requests):
    mock_requests.add(
        responses.GET,
        RANDOMLAND_RE,
        body="not json",
        content_type="application/json",
    )
    location = make_location()
    with patch(
        "plugins.gmaps.GoogleLocation.from_lat_lng", return_value=location
    ):
        result = gmaps.new_guess_game(make_bot(), "#chan", db)
    assert result == "Could not find a location with a street view image."
