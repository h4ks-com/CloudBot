"""Tests for agent URL validation, tool manifest, PR guard, and dynamic instructions."""

from unittest.mock import MagicMock, patch

import httpx

from plugins.agent import (
    _extract_urls,
    _guard_pr_hallucination,
    _tool_manifest,
    _url_validation_feedback,
    _validate_urls,
)


class TestExtractUrls:
    def test_no_urls(self):
        assert _extract_urls("hello world") == []

    def test_single_url(self):
        result = _extract_urls("see https://example.com/foo for details")
        assert result == ["https://example.com/foo"]

    def test_multiple_urls(self):
        result = _extract_urls("check https://a.com and http://b.org/page?q=1")
        assert result == ["https://a.com", "http://b.org/page?q=1"]

    def test_trailing_punctuation_stripped(self):
        result = _extract_urls("see https://example.com/page.")
        assert result == ["https://example.com/page"]

    def test_trailing_paren_stripped(self):
        result = _extract_urls("link (https://example.com/x)")
        assert result == ["https://example.com/x"]

    def test_url_with_path_and_query(self):
        result = _extract_urls(
            "deploy: https://sub.example.com/path/to/page.html?foo=bar&baz=1"
        )
        assert result == [
            "https://sub.example.com/path/to/page.html?foo=bar&baz=1"
        ]


class TestValidateUrls:
    @patch("plugins.agent.httpx.Client")
    def test_no_urls_returns_empty(self, mock_client_cls):
        assert _validate_urls("no urls here") == []
        mock_client_cls.assert_not_called()

    @patch("plugins.agent.httpx.Client")
    def test_all_urls_valid(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = MagicMock()
        mock_client.head.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _validate_urls("see https://example.com and https://other.com")
        assert result == []

    @patch("plugins.agent.httpx.Client")
    def test_dead_url_detected(self, mock_client_cls):
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_404 = MagicMock()
        mock_response_404.status_code = 404
        mock_client = MagicMock()
        mock_client.head.side_effect = [mock_response_200, mock_response_404]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _validate_urls(
            "see https://real.com and https://fake.com/page"
        )
        assert result == ["https://fake.com/page"]

    @patch("plugins.agent.httpx.Client")
    def test_timeout_let_through(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.head.side_effect = httpx.TimeoutException("timed out")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _validate_urls("https://slow.com")
        assert result == []

    @patch("plugins.agent.httpx.Client")
    def test_connection_error_let_through(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.head.side_effect = httpx.ConnectError("refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _validate_urls("https://nonexistent.invalid")
        assert result == []

    @patch("plugins.agent.httpx.Client")
    def test_tool_urls_skipped(self, mock_client_cls):
        mock_response_500 = MagicMock()
        mock_response_500.status_code = 500
        mock_client = MagicMock()
        mock_client.head.return_value = mock_response_500
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _validate_urls(
            "https://s.h4ks.com/ABC.html check this",
            tool_urls={"https://s.h4ks.com/ABC.html"},
        )
        assert result == []
        mock_client.head.assert_not_called()


class TestUrlValidationFeedback:
    def test_feedback_message(self):
        msg = _url_validation_feedback(
            ["https://fake.com/a", "https://fake.com/b"]
        )
        assert "https://fake.com/a" in msg
        assert "https://fake.com/b" in msg
        assert "HTTP errors" in msg
        assert "tool" in msg.lower()


class TestToolManifest:
    def test_empty_tracker(self):
        tracker = MagicMock()
        tracker._results = []
        assert _tool_manifest(tracker) == ""

    def test_single_tool(self):
        tracker = MagicMock()
        tracker._results = [("web_app", "https://example.com/app.html")]
        result = _tool_manifest(tracker)
        assert "web_app" in result
        assert "https://example.com/app.html" in result

    def test_multiple_tools(self):
        tracker = MagicMock()
        tracker._results = [
            ("web_app", "https://example.com/app.html"),
            (
                "describe_image",
                "A mountain landscape with a fjord and blue sky",
            ),
        ]
        result = _tool_manifest(tracker)
        assert "web_app" in result
        assert "describe_image" in result
        assert ";" in result

    def test_long_result_truncated(self):
        tracker = MagicMock()
        tracker._results = [("web_app", "x" * 200)]
        result = _tool_manifest(tracker)
        assert "..." in result

    def test_newlines_stripped(self):
        tracker = MagicMock()
        tracker._results = [("some_tool", "line1\nline2\nline3")]
        result = _tool_manifest(tracker)
        assert "\n" not in result


class TestGuardPrHallucination:
    def test_no_pr_urls_no_tool(self):
        answer = _guard_pr_hallucination(
            "I opened a PR!", [], pr_tool_called=False
        )
        assert answer == "I opened a PR!"

    def test_real_url_present(self):
        url = "https://github.com/owner/repo/pull/42"
        answer = _guard_pr_hallucination(
            f"PR opened: {url}", [url], pr_tool_called=True
        )
        assert url in answer

    def test_real_url_prepended_if_missing(self):
        url = "https://github.com/owner/repo/pull/42"
        answer = _guard_pr_hallucination(
            "all done!", [url], pr_tool_called=True
        )
        assert url in answer
        assert answer.startswith(f"PR opened: {url}")

    def test_hallucinated_url_flagged(self):
        answer = _guard_pr_hallucination(
            "PR opened: https://github.com/owner/repo/pull/99",
            [],
            pr_tool_called=True,
        )
        assert "failed to open PR" in answer
        assert "<no-pr>" in answer
