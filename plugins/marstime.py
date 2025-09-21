"""
Mars time plugin for CloudBot IRC bot.
Provides Mars Coordinated Time (MTC) and local Mars times for various mission locations.
Based on algorithms from NASA GISS Mars24 tool.
"""

from datetime import datetime, timezone
from typing import Any

from cloudbot import hook

# Mars mission locations (longitude, latitude, mission start date, name)
MARS_LOCATIONS: dict[str, dict[str, Any]] = {
    "zhurong": {
        "name": "Zhurong",
        "lon": 250.076,
        "lat": 25.048,
        "epoch": "2021-05-14T23:18:00Z",
        "description": "Chinese rover in Utopia Planitia",
    },
    "ingenuity": {
        "name": "Ingenuity",
        "lon": 282.5493,
        "lat": 18.4372,
        "epoch": "2021-02-18T20:55:00Z",
        "description": "Mars helicopter at Jezero Crater",
    },
    "perseverance": {
        "name": "Perseverance",
        "lon": 282.5492,
        "lat": 18.4447,
        "epoch": "2021-02-18T20:55:00Z",
        "description": "NASA rover at Jezero Crater",
    },
    "insight": {
        "name": "InSight",
        "lon": 224.3766,
        "lat": 4.5024,
        "epoch": "2018-11-26T19:52:59Z",
        "description": "NASA lander at Elysium Planitia",
    },
    "curiosity": {
        "name": "Curiosity",
        "lon": 222.5583,
        "lat": -4.5985,
        "epoch": "2012-08-06T05:17:57Z",
        "description": "NASA rover at Gale Crater",
    },
}


class MarsTime:
    """
    Mars time calculator based on NASA GISS Mars24 algorithms.
    Implements Mean Solar Time calculations for Mars.
    """

    # Constants for Mars time calculations
    MARS_EPOCH_JD = (
        2405522.0028779  # Julian date of Mars epoch (Dec 29, 1873 12:00 TT)
    )
    SECONDS_PER_SOL = 88775.244147  # Seconds in a Mars solar day

    def __init__(self, earth_time: datetime | None = None) -> None:
        """Initialize with Earth time (UTC), defaults to current time."""
        self.earth_time = earth_time or datetime.now(timezone.utc)

    def julian_date(self) -> float:
        """Convert Earth time to Julian Date."""
        year = self.earth_time.year
        month = self.earth_time.month
        day = self.earth_time.day
        hour = self.earth_time.hour
        minute = self.earth_time.minute
        second = self.earth_time.second + self.earth_time.microsecond / 1e6

        # Convert to Julian Date
        if month <= 2:
            year -= 1
            month += 12

        a = int(year / 100)
        b = 2 - a + int(a / 4)

        jd = (
            int(365.25 * (year + 4716))
            + int(30.6001 * (month + 1))
            + day
            + b
            - 1524.5
        )
        jd += (hour + minute / 60.0 + second / 3600.0) / 24.0

        return jd

    def mars_sol_date(self) -> float:
        """Calculate Mars Sol Date (MSD) - number of sols since Mars epoch."""
        jd = self.julian_date()
        # Terrestrial Time correction (approximate)
        jd_tt = jd + 69.184 / 86400.0
        return (jd_tt - self.MARS_EPOCH_JD) / (self.SECONDS_PER_SOL / 86400.0)

    def coordinated_mars_time(self) -> tuple[int, int, float]:
        """
        Calculate Mars Coordinated Time (MTC) at longitude 0°.
        Returns (hour, minute, second).
        """
        msd = self.mars_sol_date()
        mct = (24 * msd) % 24  # MTC in decimal hours

        hour = int(mct)
        minute = int((mct - hour) * 60)
        second = ((mct - hour) * 60 - minute) * 60

        return hour, minute, second

    def local_mars_time(self, longitude: float) -> tuple[int, int, float]:
        """
        Calculate Local Mean Solar Time (LMST) for given longitude.
        Longitude in degrees (West positive).
        Returns (hour, minute, second).
        """
        hour, minute, second = self.coordinated_mars_time()

        # Convert longitude to time offset (longitude degrees / 15 degrees per hour)
        time_offset = longitude / 15.0

        # Calculate local time
        local_decimal_hours = (
            hour + minute / 60.0 + second / 3600.0 - time_offset
        ) % 24

        local_hour = int(local_decimal_hours)
        local_minute = int((local_decimal_hours - local_hour) * 60)
        local_second = (
            (local_decimal_hours - local_hour) * 60 - local_minute
        ) * 60

        return local_hour, local_minute, local_second

    def mars_year(self) -> int:
        """Calculate current Mars Year (MY) starting from MY1 on April 11, 1955."""
        # Mars Year 1 started at Ls = 0° on April 11, 1955
        my1_jd = 2435230.5  # Julian date for April 11, 1955
        jd = self.julian_date()

        # Approximate Mars years (687 Earth days per Mars year)
        mars_years_elapsed = (jd - my1_jd) / 687.0
        return int(mars_years_elapsed) + 1

    def solar_longitude(self) -> float:
        """Calculate approximate Solar Longitude (Ls) in degrees."""
        msd = self.mars_sol_date()
        # Simplified calculation - not fully accurate but reasonable approximation
        ls = (msd * 0.9856) % 360  # Approximate daily motion
        return ls

    def format_time(self, hour: int, minute: int, second: float) -> str:
        """Format Mars time as HH:MM:SS string."""
        return f"{hour:02d}:{minute:02d}:{int(second):02d}"


def get_season_name(ls: float) -> str:
    """Get season name from Solar Longitude (Northern Hemisphere)."""
    if ls < 90:
        return "Spring"
    elif ls < 180:
        return "Summer"
    elif ls < 270:
        return "Fall"
    else:
        return "Winter"


@hook.command("marstime", "mars", autohelp=False)
def marstime(text: str) -> str:
    """
    <location> - Get Mars time. Use without args for MTC, or specify: zhurong, ingenuity,
    perseverance, insight, curiosity for mission-specific local times.
    """
    mars_calc = MarsTime()

    if not text.strip():
        # Return Mars Coordinated Time (MTC)
        hour, minute, second = mars_calc.coordinated_mars_time()
        my = mars_calc.mars_year()
        ls = mars_calc.solar_longitude()
        season = get_season_name(ls)

        time_str = mars_calc.format_time(hour, minute, second)
        return f"\x02Mars Coordinated Time (MTC):\x02 {time_str} | \x02MY:\x02 {my} | \x02Ls:\x02 {ls:.1f}° ({season})"

    # Look for specific location
    location_key = text.strip().lower()
    if location_key not in MARS_LOCATIONS:
        # Show available locations
        locations = ", ".join(MARS_LOCATIONS.keys())
        return f"Unknown location '{text}'. Available locations: {locations}"

    location = MARS_LOCATIONS[location_key]
    hour, minute, second = mars_calc.local_mars_time(location["lon"])
    my = mars_calc.mars_year()
    ls = mars_calc.solar_longitude()
    season = get_season_name(ls)

    time_str = mars_calc.format_time(hour, minute, second)

    return (
        f"\x02{location['name']} Local Solar Time:\x02 {time_str} | "
        f"\x02Coordinates:\x02 {abs(location['lat']):.1f}°{'N' if location['lat'] >= 0 else 'S'}, "
        f"{location['lon']:.1f}°W | \x02MY:\x02 {my} | \x02Ls:\x02 {ls:.1f}° ({season})"
    )


@hook.command("marslocations", "marslocs", autohelp=False)
def mars_locations(reply) -> None:
    """List available Mars mission locations for marstime command."""
    reply("Available Mars locations:")
    for key, data in MARS_LOCATIONS.items():
        reply(f"\x02{key}\x02: {data['name']} - {data['description']}")
