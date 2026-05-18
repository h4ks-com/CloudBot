"""NASA ISS Telemetry Plugin for CloudBot
Provides real-time ISS telemetry data including urine tank levels.
"""

import asyncio
from collections.abc import Callable
from typing import TypedDict

import aiohttp
from lightstreamer.client import LightstreamerClient, Subscription

from cloudbot import hook
from cloudbot.util.formatting import IRC_TAGS


def bold(text):
    """Make text bold for IRC."""
    return f"{IRC_TAGS['b']}{text}{IRC_TAGS['b']}"


class TelemetryEntry(TypedDict):
    node: str
    format: Callable[[float], str]
    error: str


# Telemetry configuration mapping
TELEMETRY_CONFIG: dict[str, TelemetryEntry] = {
    "temp": {
        "node": "USLAB000059",
        "format": lambda x: f"🌡️ ISS Cabin Temperature: {bold(f'{x:.1f}°C')} ({bold(f'{(x * 9/5) + 32:.1f}°F')})",
        "error": "❌ Unable to retrieve temperature data",
    },
    "pressure": {
        "node": "USLAB000058",
        "format": lambda x: f"🔘 ISS Cabin Pressure: {bold(f'{x:.1f} mmHg')}",
        "error": "❌ Unable to retrieve pressure data",
    },
    "co2": {
        "node": "NODE3000003",
        "format": lambda x: f"💨 ISS CO2 Level: {bold(f'{x:.1f} mmHg')}",
        "error": "❌ Unable to retrieve CO2 data",
    },
    "oxygen": {
        "node": "NODE3000001",
        "format": lambda x: f"💨 ISS Oxygen Level: {bold(f'{x:.1f} mmHg')}",
        "error": "❌ Unable to retrieve oxygen data",
    },
    "urine": {
        "node": "NODE3000005",
        "format": lambda x: f"🚽 ISS Urine Tank Level: {bold(f'{x}%')}",
        "error": "❌ Unable to retrieve urine tank data",
    },
}


class ISSDataManager:
    """Manages ISS telemetry data connection and caching."""

    def __init__(self):
        self.client = None
        self.subscription = None
        self.telemetry_data = {}
        self.connected = False
        self._connect_lock = asyncio.Lock()

    async def ensure_connected(self):
        """Ensure connection to NASA telemetry stream."""
        if self.connected and self.client:
            return True

        async with self._connect_lock:
            if self.connected and self.client:
                return True

            return await self._connect()

    async def _connect(self):
        """Connect to NASA's ISS telemetry stream."""
        try:
            self.client = LightstreamerClient(
                "https://push.lightstreamer.com", "ISSLIVE"
            )

            connection_future: asyncio.Future[bool] = asyncio.Future()

            class ConnectionListener:
                def onStatusChange(self, new_status):
                    if new_status == "CONNECTED:WS-STREAMING":
                        if not connection_future.done():
                            connection_future.set_result(True)
                    elif new_status.startswith("DISCONNECTED"):
                        if not connection_future.done():
                            connection_future.set_result(False)

            self.client.addListener(ConnectionListener())
            self.client.connect()

            # Wait for connection with timeout
            try:
                result = await asyncio.wait_for(connection_future, timeout=10.0)
                if result:
                    self.connected = True
                    await self._subscribe_telemetry()
                    return True
            except asyncio.TimeoutError:
                pass

        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError):
            return False

        return False

    async def _subscribe_telemetry(self):
        """Subscribe to ISS telemetry data."""
        if not self.client:
            return

        # Subscribe to all configured telemetry points
        nodes = [config["node"] for config in TELEMETRY_CONFIG.values()]

        self.subscription = Subscription("MERGE", nodes, ["Value"])

        class TelemetryListener:
            def __init__(self, manager):
                self.manager = manager

            def onItemUpdate(self, update):
                item_name = update.getItemName()
                value = update.getValue("Value")
                if value is not None:
                    try:
                        self.manager.telemetry_data[item_name] = float(value)
                    except (ValueError, TypeError):
                        pass

        self.subscription.addListener(TelemetryListener(self))
        self.client.subscribe(self.subscription)

    async def get_telemetry(self, node_id):
        """Get current telemetry value for a specific node."""
        if await self.ensure_connected():
            # Wait briefly for data if we just connected
            if node_id not in self.telemetry_data:
                await asyncio.sleep(2)
            return self.telemetry_data.get(node_id)
        return None

    def disconnect(self):
        """Disconnect from telemetry stream."""
        if self.subscription and self.client:
            self.client.unsubscribe(self.subscription)
        if self.client:
            self.client.disconnect()
        self.connected = False


# Global ISS data manager instance
iss_manager = ISSDataManager()


def get_ocean_name(latitude, longitude):
    """Determine which ocean/sea based on coordinates."""
    lat, lon = float(latitude), float(longitude)

    # Arctic Ocean - highest priority
    if lat >= 66:
        return "Arctic Ocean"

    # Antarctic/Southern Ocean
    if lat <= -60:
        return "Southern Ocean"

    # Mediterranean Sea
    if 5 <= lon <= 36 and 30 <= lat <= 46:
        return "Mediterranean Sea"

    # Red Sea
    if 32 <= lon <= 43 and 12 <= lat <= 30:
        return "Red Sea"

    # Atlantic Ocean (including western boundary)
    if -80 <= lon <= 20:
        return "Atlantic Ocean"

    # Indian Ocean
    if 20 <= lon <= 120:
        return "Indian Ocean"

    # Pacific Ocean (everything else - largest ocean)
    if (-180 <= lon <= -80) or (120 <= lon <= 180):
        return "Pacific Ocean"

    # Default fallback
    return "International Waters"


async def get_iss_location():
    """Get current ISS position, altitude, velocity and location."""
    async with aiohttp.ClientSession() as session:
        # Get ISS position data
        async with session.get(
            "https://api.wheretheiss.at/v1/satellites/25544"
        ) as response:
            if response.status != 200:
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                )
            iss_data = await response.json()

        latitude = iss_data["latitude"]
        longitude = iss_data["longitude"]
        altitude = iss_data["altitude"]  # km
        velocity = iss_data["velocity"]  # km/h

        # Get location name from coordinates
        location = "Unknown"
        geocode_url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={latitude}&longitude={longitude}&localityLanguage=en"
        async with session.get(geocode_url) as geo_response:
            if geo_response.status == 200:
                geo_data = await geo_response.json()

                # Check if over ocean/international waters
                if geo_data.get("countryCode") == "":
                    location = get_ocean_name(latitude, longitude)
                else:
                    country = geo_data.get("countryName", "Unknown")
                    city = geo_data.get("city", "")
                    if city and city != country:
                        location = f"{city}, {country}"
                    else:
                        location = country

        return {
            "latitude": latitude,
            "longitude": longitude,
            "altitude": altitude,
            "velocity": velocity,
            "location": location,
        }


@hook.on_stop()
def cleanup():
    """Clean up connections when bot shuts down."""
    iss_manager.disconnect()


@hook.command("piss", autohelp=False)
async def iss_piss_level():
    """- Returns the current ISS urine tank level percentage"""

    try:
        urine_level = await iss_manager.get_telemetry("NODE3000005")

        if urine_level is not None:
            # Format the response with appropriate emoji based on level
            if urine_level >= 80:
                emoji = "🚨"  # Very full
            elif urine_level >= 60:
                emoji = "⚠️"  # Getting full
            elif urine_level >= 40:
                emoji = "📊"  # Moderate
            elif urine_level >= 20:
                emoji = "📉"  # Low
            else:
                emoji = "✅"  # Very low

            return f"{emoji} ISS Urine Tank Level: {bold(f'{urine_level}%')} 🚽"
        else:
            return "❌ Unable to retrieve ISS telemetry data"

    except (ConnectionError, asyncio.TimeoutError) as e:
        return f"🚫 Error accessing ISS telemetry: {type(e).__name__}"


@hook.command("iss", autohelp=False)
async def iss_telemetry(text):
    """<subcommand> - ISS telemetry data. Use 'list' to see available commands."""

    if not text:
        # Show current ISS location and status
        try:
            location_data = await get_iss_location()
            lat = location_data["latitude"]
            lon = location_data["longitude"]
            alt = location_data["altitude"]
            vel = location_data["velocity"]
            loc = location_data["location"]

            return (
                f"🛰️ ISS Location: {bold(f'{lat:.2f}°, {lon:.2f}°')} | "
                f"Altitude: {bold(f'{alt:.0f}km')} | Speed: {bold(f'{vel:.0f}km/h')} | "
                f"Over: {bold(loc)}"
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError) as e:
            return (
                f"❌ Unable to retrieve ISS location data: {type(e).__name__}"
            )

    subcommand = text.strip().lower()

    match subcommand:
        case "list":
            commands = ", ".join(TELEMETRY_CONFIG.keys())
            return f"Available commands: {bold(commands)}, {bold('source')}"

        case "source":
            return f"🛰️ {bold('NASA live telemetry')} - https://iss-mimic.github.io/Mimic/ - directly from https://push.lightstreamer.com ISSLIVE"

        case "pissinfo":
            return "https://bsky.app/profile/iss-piss-tracker.bsky.social/post/3lxnr3lttrs2k"

        case cmd if cmd in TELEMETRY_CONFIG:
            config = TELEMETRY_CONFIG[cmd]
            try:
                value = await iss_manager.get_telemetry(config["node"])
                if value is not None:
                    return config["format"](value)
                else:
                    return config["error"]
            except (ConnectionError, asyncio.TimeoutError) as e:
                return f"🚫 Connection error: {type(e).__name__}"

        case _:
            return (
                "❓ Unknown subcommand. Use '.iss list' for available commands"
            )
