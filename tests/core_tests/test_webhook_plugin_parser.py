from cloudbot.webhooks.plugin_parser import PluginParser


def _commands(tmp_path):
    return {
        command.name: command
        for plugin in PluginParser(str(tmp_path)).parse_all_plugins()
        for command in plugin.commands
    }


def test_parser_extracts_async_and_sync_commands(tmp_path):
    (tmp_path / "myplugin.py").write_text(
        "from cloudbot import hook\n\n\n"
        "@hook.command('ask', 'agent', 'agi')\n"
        "async def agent_command(text):\n"
        "    '''<prompt> - ask the bot'''\n"
        "    return text\n\n\n"
        "@hook.command('ping')\n"
        "def ping(text):\n"
        "    '''- pong'''\n"
        "    return 'pong'\n"
    )
    commands = _commands(tmp_path)

    assert set(commands) == {"ask", "ping"}
    assert commands["ask"].aliases == ["agent", "agi"]
    assert commands["ask"].function_name == "agent_command"
    assert "ask the bot" in commands["ask"].docstring


def test_parser_uses_function_name_when_decorator_has_no_args(tmp_path):
    (tmp_path / "p.py").write_text(
        "from cloudbot import hook\n\n\n"
        "@hook.command()\n"
        "async def weather(text):\n"
        "    '''- weather'''\n\n\n"
        "@hook.command\n"
        "def ping(text):\n"
        "    '''- pong'''\n"
    )
    commands = _commands(tmp_path)

    assert set(commands) == {"weather", "ping"}
    assert commands["weather"].aliases == []


def test_parser_honours_blacklist(tmp_path):
    (tmp_path / "stuff.py").write_text(
        "from cloudbot import hook\n\n\n"
        "@hook.command('help')\n"
        "async def help_command(text):\n"
        "    '''- help'''\n"
    )
    assert _commands(tmp_path) == {}
