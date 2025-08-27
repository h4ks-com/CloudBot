"""NASA ISS Telemetry Plugin for CloudBot
Provides real-time ISS telemetry data including urine tank levels.
"""

import asyncio

from lightstreamer.client import LightstreamerClient, Subscription

from cloudbot import hook


class ISSDataManager:
    """Manages ISS telemetry data connection and caching."""

    def __init__(self):
        self.client = None
        self.urine_subscription = None
        self.urine_level = None
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

        # Subscribe to urine tank level (NODE3000005)
        self.urine_subscription = Subscription("MERGE", ["NODE3000005"], ["Value"])

        class TelemetryListener:
            def __init__(self, manager):
                self.manager = manager

            def onItemUpdate(self, update):
                value = update.getValue("Value")
                if value is not None:
                    try:
                        self.manager.urine_level = float(value)
                    except (ValueError, TypeError):
                        pass

        self.urine_subscription.addListener(TelemetryListener(self))
        self.client.subscribe(self.urine_subscription)

    async def get_urine_level(self):
        """Get current ISS urine tank level percentage."""
        if await self.ensure_connected():
            # Wait briefly for data if we just connected
            if self.urine_level is None:
                await asyncio.sleep(2)
            return self.urine_level
        return None

    def disconnect(self):
        """Disconnect from telemetry stream."""
        if self.urine_subscription and self.client:
            self.client.unsubscribe(self.urine_subscription)
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
        urine_level = await iss_manager.get_urine_level()

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
