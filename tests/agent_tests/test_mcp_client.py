"""Tests for the generic MCP client and the tools it builds from a server."""

import pytest
import requests
from responses import matchers

from cloudbot.agent.mcp_client import (
    MCPServer,
    extract_mcp_content,
    parse_sse,
    parse_sse_error,
    server_manifest,
)
from cloudbot.agent.tools import mcp_servers
from cloudbot.agent.tools.mcp_servers import (
    build_mcp_tools,
    discover,
    load_servers,
)

MCP_URL = "https://example.com/mcp"

SSE = "text/event-stream"

INIT = 'data: {"result":{"instructions":"a midi search engine"},"id":0}\n\n'
LISTED = (
    'data: {"result":{"tools":[{"name":"search_midi",'
    '"description":"find midi files",'
    '"inputSchema":{"$schema":"http://json-schema.org/draft-07/schema#",'
    '"type":"object","properties":{"q":{"type":"string"}},'
    '"required":["q"]}}]},"id":1}\n\n'
)

SERVER = MCPServer(name="kin", url=MCP_URL, headers={}, timeout_s=30)


@pytest.fixture(autouse=True)
def clear_manifest_cache():
    mcp_servers._MANIFESTS.clear()
    yield
    mcp_servers._MANIFESTS.clear()


def configure(mock_bot, servers, keys=None):
    mock_bot.config["plugins"] = {"agent": {"mcp_servers": servers}}
    mock_bot.config["api_keys"] = keys or {}
    mock_bot.config.load_config()
    return mock_bot


def serve(mock_requests, *bodies):
    """Answer the initialize handshake, then each call that follows it."""
    for body in bodies:
        mock_requests.add("POST", MCP_URL, body=body, content_type=SSE)


class TestLoadServers:
    def test_reads_a_public_server(self, mock_bot):
        bot = configure(mock_bot, {"kin": {"url": MCP_URL}})
        [server] = load_servers(bot)
        assert server.name == "kin"
        assert server.url == MCP_URL
        assert server.headers == {}
        assert server.timeout_s == 30

    def test_skips_a_disabled_server(self, mock_bot):
        bot = configure(mock_bot, {"kin": {"url": MCP_URL, "enabled": False}})
        assert load_servers(bot) == []

    def test_skips_a_server_with_no_url(self, mock_bot):
        bot = configure(mock_bot, {"kin": {"enabled": True}})
        assert load_servers(bot) == []

    def test_carries_an_api_key_in_the_named_header(self, mock_bot):
        bot = configure(
            mock_bot,
            {
                "kin": {
                    "url": MCP_URL,
                    "api_key_config_path": "kin_key",
                    "api_key_header": "x-key",
                }
            },
            {"kin_key": "secret"},
        )
        [server] = load_servers(bot)
        assert server.headers == {"x-key": "secret"}

    def test_carries_a_bearer_token(self, mock_bot):
        bot = configure(
            mock_bot,
            {"kin": {"url": MCP_URL, "bearer_token_config_path": "kin_token"}},
            {"kin_token": "tok"},
        )
        [server] = load_servers(bot)
        assert server.headers == {"Authorization": "Bearer tok"}

    def test_skips_a_server_whose_key_is_unset(self, mock_bot):
        bot = configure(
            mock_bot,
            {"kin": {"url": MCP_URL, "api_key_config_path": "missing"}},
        )
        assert load_servers(bot) == []

    def test_reads_every_configured_server(self, mock_bot):
        bot = configure(
            mock_bot,
            {
                "one": {"url": "https://one.example/mcp"},
                "two": {"url": "https://two.example/mcp"},
            },
        )
        assert [s.name for s in load_servers(bot)] == ["one", "two"]


class TestParsing:
    def test_reads_a_result_out_of_an_sse_body(self):
        body = 'event: message\ndata: {"result":{"tools":[]},"id":1}\n\n'
        assert parse_sse(body) == {"tools": []}

    def test_returns_none_when_the_stream_has_no_result(self):
        assert parse_sse("event: ping\n\n") is None

    def test_reads_an_error_message_out_of_an_sse_body(self):
        body = (
            'data: {"error":{"code":-32602,"message":"missing q"},"id":1}\n\n'
        )
        assert parse_sse_error(body) == "missing q"

    def test_extracts_text_content(self):
        result = {"content": [{"type": "text", "text": "found it"}]}
        assert extract_mcp_content(result) == "found it"

    def test_marks_a_tool_error_as_an_error(self):
        result = {
            "isError": True,
            "content": [{"type": "text", "text": "nope"}],
        }
        assert extract_mcp_content(result) == "(error: nope)"


class TestServerManifest:
    def test_reports_what_the_server_says_it_is(self, mock_requests):
        serve(mock_requests, INIT, LISTED)
        instructions, tools = server_manifest(SERVER)
        assert instructions == "a midi search engine"
        assert [t["name"] for t in tools] == ["search_midi"]

    def test_carries_the_session_id_the_server_handed_out(self, mock_requests):
        mock_requests.add(
            "POST",
            MCP_URL,
            body=INIT,
            headers={"mcp-session-id": "sess-1"},
            content_type=SSE,
        )
        mock_requests.add(
            "POST",
            MCP_URL,
            body=LISTED,
            content_type=SSE,
            match=[matchers.header_matcher({"mcp-session-id": "sess-1"})],
        )
        _, tools = server_manifest(SERVER)
        assert [t["name"] for t in tools] == ["search_midi"]

    def test_a_server_that_is_down_offers_nothing(self, mock_requests):
        mock_requests.add(
            "POST", MCP_URL, body=requests.ConnectionError("refused")
        )
        assert server_manifest(SERVER) == ("", [])


class TestBuildMcpTools:
    def test_namespaces_the_tool_and_carries_the_server_context(
        self, mock_bot, mock_requests
    ):
        bot = configure(mock_bot, {"kin": {"url": MCP_URL}})
        serve(mock_requests, INIT, LISTED)
        discover(bot)
        [built] = build_mcp_tools(bot)
        assert built.name == "kin_search_midi"
        assert "find midi files" in built.description
        assert "a midi search engine" in built.description

    def test_drops_the_schema_dialect_the_agent_sdk_rejects(
        self, mock_bot, mock_requests
    ):
        bot = configure(mock_bot, {"kin": {"url": MCP_URL}})
        serve(mock_requests, INIT, LISTED)
        discover(bot)
        [built] = build_mcp_tools(bot)
        assert "$schema" not in built.params_json_schema
        assert built.params_json_schema["required"] == ["q"]
        assert built.strict_json_schema is False

    def test_builds_nothing_before_discovery_and_never_calls_out(
        self, mock_bot
    ):
        """Building the tool list runs on the event loop, so it stays offline."""
        bot = configure(mock_bot, {"kin": {"url": MCP_URL}})
        assert build_mcp_tools(bot) == []
