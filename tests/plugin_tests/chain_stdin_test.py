from unittest.mock import AsyncMock, MagicMock, call

import pytest

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.plugin import Plugin
from cloudbot.plugin_hooks import CommandHook
# Mock helper to extract hook
from cloudbot.util import HOOK_ATTR
from plugins import chain


def _get_hook(func, name):
    return getattr(func, HOOK_ATTR)[name]


@pytest.mark.asyncio
async def test_chain_stdin(mock_bot_factory):
    mock_bot = mock_bot_factory()

    # Define a command that accepts stdin
    @hook.command("consumer")
    def consumer(text, stdin=None):
        pass  # mock implementation

    # Define a command that produces output
    @hook.command("producer")
    def producer(text):
        pass

    # Register them
    plugin = Plugin(
        "plugins/test_chain.py", "test_chain.py", "test_chain", MagicMock()
    )

    consumer_hook = CommandHook(plugin, _get_hook(consumer, "command"))
    producer_hook = CommandHook(plugin, _get_hook(producer, "command"))

    mock_bot.plugin_manager.commands["consumer"] = consumer_hook
    mock_bot.plugin_manager.commands["producer"] = producer_hook

    # Allow them in chain
    chain.allow_cache["test_chain.consumer"] = True
    chain.allow_cache["test_chain.producer"] = True

    # Mock internal_launch
    async def side_effect(hook, event):
        if hook == producer_hook:
            return True, "hello world"
        if hook == consumer_hook:
            # check if stdin is passed correctly
            if event.stdin == "hello world":
                return True, "consumed hello world"
            else:
                return True, f"fail: {event.stdin}"
        return False, None

    mock_bot.plugin_manager.internal_launch = AsyncMock(side_effect=side_effect)

    event = MagicMock()
    # Mock event methods
    event.message = MagicMock()
    event.reply = MagicMock()
    event.notice = MagicMock()
    event.check_permissions = AsyncMock(return_value=True)

    # Run chain
    await chain.chain("producer | consumer", mock_bot, event)

    # Check output
    # event.reply("consumed hello world") should be called.
    event.reply.assert_called_with("consumed hello world", target=None)

    # Check if consumer received stdin
    call_args_list = mock_bot.plugin_manager.internal_launch.call_args_list
    assert len(call_args_list) == 2
    consumer_call = call_args_list[1]
    # args: hook, event
    event_arg = consumer_call[0][1]
    assert isinstance(event_arg, CommandEvent)
    assert event_arg.stdin == "hello world"


@pytest.mark.asyncio
async def test_chain_no_stdin(mock_bot_factory):
    mock_bot = mock_bot_factory()

    @hook.command("old_consumer")
    def old_consumer(text):
        pass

    @hook.command("producer")
    def producer(text):
        pass

    plugin = Plugin(
        "plugins/test_chain.py", "test_chain.py", "test_chain", MagicMock()
    )

    old_consumer_hook = CommandHook(plugin, _get_hook(old_consumer, "command"))
    producer_hook = CommandHook(plugin, _get_hook(producer, "command"))

    mock_bot.plugin_manager.commands["old_consumer"] = old_consumer_hook
    mock_bot.plugin_manager.commands["producer"] = producer_hook

    chain.allow_cache["test_chain.old_consumer"] = True
    chain.allow_cache["test_chain.producer"] = True

    async def side_effect(hook, event):
        if hook == producer_hook:
            return True, "hello"
        if hook == old_consumer_hook:
            # check text args
            return True, f"consumed {event.text}"
        return False, None

    mock_bot.plugin_manager.internal_launch = AsyncMock(side_effect=side_effect)

    event = MagicMock()
    event.message = MagicMock()
    event.reply = MagicMock()
    event.notice = MagicMock()
    event.check_permissions = AsyncMock(return_value=True)

    await chain.chain("producer | old_consumer", mock_bot, event)

    call_args_list = mock_bot.plugin_manager.internal_launch.call_args_list
    consumer_call = call_args_list[1]
    event_arg = consumer_call[0][1]
    assert event_arg.stdin is None
    assert event_arg.text.strip() == "hello"  # piped output appended to text

    event.reply.assert_called_with("consumed hello", target=None)
