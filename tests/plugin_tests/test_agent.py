"""Tests for the agent tool manifest, PR guard, and answer formatting."""

from unittest.mock import MagicMock, patch

from plugins.agent import (
    _format_answer,
    _guard_pr_hallucination,
    _tool_manifest,
)


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


class TestFormatAnswer:
    cfg = {"reply_max_chars": 420, "reply_max_lines": 10}

    def test_multiline_keeps_structure_and_pings_own_line(self):
        text = "first line\nsecond line\nthird line"
        assert _format_answer(text, self.cfg) == (
            ["first line", "second line", "third line"],
            True,
        )

    def test_single_line_pings_inline(self):
        assert _format_answer("just one", self.cfg) == (["just one"], False)

    @patch("plugins.agent.upload_markdown_paste", return_value="http://p/x")
    def test_overflow_leads_with_paste_link_inline(self, _paste):
        text = "\n".join(f"line {i}" for i in range(25))
        messages, ping_own_line = _format_answer(text, self.cfg)
        assert ping_own_line is False
        assert messages[0] == "full: http://p/x"
        assert messages[1:] == [f"line {i}" for i in range(10)]

    def test_empty_text_returns_empty(self):
        assert _format_answer("   ", self.cfg) == ([], False)
