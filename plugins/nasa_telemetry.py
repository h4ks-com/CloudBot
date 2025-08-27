"""NASA ISS Telemetry Plugin for CloudBot
Provides real-time ISS telemetry data including urine tank levels.
"""

import asyncio

from lightstreamer.client import LightstreamerClient, Subscription

from cloudbot import hook


# Telemetry configuration mapping
TELEMETRY_CONFIG = {
    "temp": {
        "node": "USLAB000059",
        "format": lambda x: f"🌡️ ISS Cabin Temperature: {x:.1f}°C ({(x * 9/5) + 32:.1f}°F)",
        "error": "❌ Unable to retrieve temperature data"
    },
    "pressure": {
        "node": "USLAB000058",
        "format": lambda x: f"🔘 ISS Cabin Pressure: {x:.1f} mmHg",
        "error": "❌ Unable to retrieve pressure data"
    },
    "co2": {
        "node": "NODE3000003",
        "format": lambda x: f"💨 ISS CO2 Level: {x:.1f} mmHg",
        "error": "❌ Unable to retrieve CO2 data"
    },
    "oxygen": {
        "node": "NODE3000001",
        "format": lambda x: f"💨 ISS Oxygen Level: {x:.1f} mmHg",
        "error": "❌ Unable to retrieve oxygen data"
    },
    "urine": {
        "node": "NODE3000005",
        "format": lambda x: f"🚽 ISS Urine Tank Level: {x}%",
        "error": "❌ Unable to retrieve urine tank data"
    }
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
            self.client = LightstreamerClient("https://push.lightstreamer.com", "ISSLIVE")

            connection_future = asyncio.Future()

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

        except Exception:
            pass

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

            return f"{emoji} ISS Urine Tank Level: {urine_level}% 🚽"
        else:
            return "❌ Unable to retrieve ISS telemetry data"

    except Exception as e:
        return f"🚫 Error accessing ISS telemetry: {type(e).__name__}"


@hook.command("iss")
async def iss_telemetry(text):
    """<subcommand> - ISS telemetry data. Use 'list' to see available commands."""

    if not text:
        return "🛰️ ISS Live Telemetry - Use '.iss list' for available commands"

    subcommand = text.strip().lower()

    match subcommand:
        case "list":
            commands = ", ".join(TELEMETRY_CONFIG.keys())
            return f"Available commands: {commands}, source"

        case "source":
            return "🛰️ NASA live telemetry - https://iss-mimic.github.io/Mimic/"

        case cmd if cmd in TELEMETRY_CONFIG:
            config = TELEMETRY_CONFIG[cmd]
            try:
                value = await iss_manager.get_telemetry(config["node"])
                if value is not None:
                    return config["format"](value)
                else:
                    return config["error"]
            except Exception as e:
                return f"🚫 Error: {type(e).__name__}"

        case _:
            return "❓ Unknown subcommand. Use '.iss list' for available commands"
