import asyncio
import logging
from unittest.mock import patch

import pytest

from cloudbot.__main__ import async_main


@pytest.mark.asyncio
async def test_main():
    async def run():
        return False

    with patch("cloudbot.__main__.CloudBot") as mocked:
        mocked().run = run
        await async_main()
        assert logging._srcfile is None
        assert not logging.logThreads
        assert not logging.logProcesses


class FakeServer:
    """Serves until told to exit, like uvicorn's Server."""

    def __init__(self, config):
        self.should_exit = False

    async def serve(self):
        while not self.should_exit:
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_main_stops_the_webhook_server_when_the_bot_stops():
    async def run():
        return False

    with (
        patch("cloudbot.__main__.CloudBot") as mocked,
        patch("cloudbot.__main__.is_enabled", return_value=True),
        patch("cloudbot.__main__.get_port", return_value=0),
        patch("cloudbot.__main__.Server", FakeServer),
    ):
        mocked().run = run
        await asyncio.wait_for(async_main(), timeout=5)
