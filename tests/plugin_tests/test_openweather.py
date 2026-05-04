from datetime import date, datetime, timezone

import pytest
from responses.matchers import query_param_matcher

from plugins import openweather

BASE_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


def make_slot(
    ts: int,
    temp: float = 18.0,
    weather_id: int = 800,
    icon: str = "01d",
) -> dict:
    return {
        "dt": ts,
        "main": {
            "temp": temp,
            "temp_min": temp - 2,
            "temp_max": temp + 2,
            "humidity": 60,
        },
        "weather": [
            {"id": weather_id, "description": "clear sky", "icon": icon}
        ],
        "wind": {"speed": 3.5},
    }


def noon_ts(year: int, month: int, day: int) -> int:
    return int(
        datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    )


def make_forecast_response(
    city: str = "Berlin", country: str = "DE", slots: list | None = None
) -> dict:
    if slots is None:
        base = noon_ts(2024, 3, 15)
        slots = [make_slot(base + i * 3 * 3600) for i in range(9)]
    return {"city": {"name": city, "country": country}, "list": slots}


# --- weather_emoji ---


@pytest.mark.parametrize(
    "weather_id,icon,expected",
    [
        (200, "11d", "⛈"),
        (299, "11n", "⛈"),
        (300, "09d", "🌦"),
        (399, "09n", "🌦"),
        (500, "10d", "🌧"),
        (599, "10n", "🌧"),
        (600, "13d", "❄️"),
        (699, "13n", "❄️"),
        (700, "50d", "🌫"),
        (799, "50n", "🌫"),
        (800, "01d", "☀️"),
        (800, "01n", "🌙"),
        (801, "02d", "🌤"),
        (802, "03d", "⛅"),
        (803, "04d", "☁️"),
        (804, "04n", "☁️"),
    ],
)
def test_weather_emoji(weather_id, icon, expected):
    assert openweather.weather_emoji(weather_id, icon) == expected


# --- forecast (fc / fcd) ---


def test_forecast_no_api_key(mock_api_keys):
    mock_api_keys.config.get_api_key.return_value = None
    result = openweather.forecast("Berlin")
    assert isinstance(result, str)
    assert "API key" in result


def test_forecast_no_city(mock_api_keys):
    result = openweather.forecast("")
    assert isinstance(result, str)
    assert "city" in result.lower()


def test_forecast_returns_lines(mock_api_keys, mock_requests):
    data = make_forecast_response()
    mock_requests.add(
        "GET",
        BASE_FORECAST_URL,
        json=data,
        match=[
            query_param_matcher(
                {
                    "q": "Berlin",
                    "appid": "APIKEY",
                    "units": "metric",
                    "cnt": "9",
                }
            )
        ],
    )

    result = openweather.forecast("Berlin")

    assert isinstance(result, list)
    # header + 3 lines (9 slots / 3 per line)
    assert len(result) == 4
    assert "Berlin" in result[0]
    assert "DE" in result[0]
    assert "🕐" in result[0]


def test_forecast_slots_contain_emojis(mock_api_keys, mock_requests):
    base = noon_ts(2024, 3, 15)
    slots = [
        make_slot(base, temp=20.0, weather_id=800, icon="01d"),
        make_slot(base + 3600 * 3, temp=16.0, weather_id=500, icon="10d"),
        make_slot(base + 3600 * 6, temp=5.0, weather_id=601, icon="13n"),
    ]
    data = make_forecast_response(slots=slots)
    mock_requests.add(
        "GET",
        BASE_FORECAST_URL,
        json=data,
        match=[
            query_param_matcher(
                {
                    "q": "Berlin",
                    "appid": "APIKEY",
                    "units": "metric",
                    "cnt": "9",
                }
            )
        ],
    )

    result = openweather.forecast("Berlin")

    assert isinstance(result, list)
    line = result[1]
    assert "☀️" in line
    assert "🌧" in line
    assert "❄️" in line


def test_forecast_city_not_found(mock_api_keys, mock_requests):
    mock_requests.add("GET", BASE_FORECAST_URL, status=404)
    result = openweather.forecast("NotACity")
    assert isinstance(result, str)
    assert "not found" in result


def test_forecast_auth_error(mock_api_keys, mock_requests):
    mock_requests.add("GET", BASE_FORECAST_URL, status=401)
    result = openweather.forecast("Berlin")
    assert isinstance(result, str)
    assert "Invalid API key" in result


# --- forecast_week (fcw) ---


def _multi_day_slots() -> list:
    """Slots spanning 4 distinct days at noon UTC."""
    slots = []
    for day in range(15, 19):  # 15, 16, 17, 18 March 2024
        base = noon_ts(2024, 3, day)
        for offset in range(4):  # 4 slots per day (every 3h around noon)
            slots.append(
                make_slot(base + offset * 3 * 3600, temp=float(15 + day - 15))
            )
    return slots


def test_forecast_week_no_api_key(mock_api_keys):
    mock_api_keys.config.get_api_key.return_value = None
    result = openweather.forecast_week("Berlin")
    assert isinstance(result, str)
    assert "API key" in result


def test_forecast_week_no_city(mock_api_keys):
    result = openweather.forecast_week("")
    assert isinstance(result, str)
    assert "city" in result.lower()


def test_forecast_week_returns_lines(mock_api_keys, mock_requests):
    data = make_forecast_response(slots=_multi_day_slots())
    mock_requests.add(
        "GET",
        BASE_FORECAST_URL,
        json=data,
        match=[
            query_param_matcher(
                {
                    "q": "Berlin",
                    "appid": "APIKEY",
                    "units": "metric",
                    "cnt": "40",
                }
            )
        ],
    )

    result = openweather.forecast_week("Berlin")

    assert isinstance(result, list)
    assert len(result) == 2
    assert "Berlin" in result[0]
    assert "📅" in result[0]


def test_forecast_week_four_days(mock_api_keys, mock_requests):
    data = make_forecast_response(slots=_multi_day_slots())
    mock_requests.add(
        "GET",
        BASE_FORECAST_URL,
        json=data,
        match=[
            query_param_matcher(
                {
                    "q": "Berlin",
                    "appid": "APIKEY",
                    "units": "metric",
                    "cnt": "40",
                }
            )
        ],
    )

    result = openweather.forecast_week("Berlin")

    # 4 days = 3 separators in the forecast line
    assert result[1].count("│") == 3


def test_forecast_week_contains_day_names(mock_api_keys, mock_requests):
    data = make_forecast_response(slots=_multi_day_slots())
    mock_requests.add(
        "GET",
        BASE_FORECAST_URL,
        json=data,
    )

    result = openweather.forecast_week("Berlin")

    forecast_line = result[1]
    # noon_ts 2024-03-15 = Friday
    days_present = [
        d
        for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        if d in forecast_line
    ]
    assert len(days_present) == 4


def test_forecast_week_city_not_found(mock_api_keys, mock_requests):
    mock_requests.add("GET", BASE_FORECAST_URL, status=404)
    result = openweather.forecast_week("Nowhere")
    assert isinstance(result, str)
    assert "not found" in result
