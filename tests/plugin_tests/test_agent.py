"""Tests for the agent tool manifest, PR guard, and answer formatting."""

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openai import OpenAIError

from plugins import agent as agent_plugin
from plugins.agent import (
    _MANIFEST_RE,
    _format_answer,
    _guard_artifact_urls,
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


class TestGuardArtifactUrls:
    shown = {"https://s.h4ks.com/Aa.mp3"}

    def test_invented_link_removed(self):
        answer = _guard_artifact_urls(
            "done! try it: https://s.h4ks.com/Aa.html", set(), self.shown
        )
        assert "<nothing-was-uploaded>" in answer
        assert "Aa.html" not in answer

    def test_own_upload_survives_a_retyped_extension(self):
        produced = {"https://s.h4ks.com/Bb.html"}
        answer = "grab https://s.h4ks.com/Bb.glb?download=true"
        assert _guard_artifact_urls(answer, produced, set()) == answer

    def test_link_already_on_screen_survives(self):
        answer = "that mp3 is https://s.h4ks.com/Aa.mp3"
        assert _guard_artifact_urls(answer, set(), self.shown) == answer

    def test_shown_link_survives_markdown_wrapping(self):
        answer = "app is `https://s.h4ks.com/Aa.mp3`!"
        assert _guard_artifact_urls(answer, set(), self.shown) == answer

    def test_relabelling_a_shown_id_is_still_caught(self):
        answer = "now at https://s.h4ks.com/Aa.html"
        assert "<nothing-was-uploaded>" in _guard_artifact_urls(
            answer, set(), self.shown
        )

    def test_foreign_urls_untouched(self):
        answer = "see https://example.com/x and http://localhost:8000/y"
        assert _guard_artifact_urls(answer, set(), set()) == answer

    def test_a_games_upload_survives_a_subpath(self):
        produced = {"https://snake.games.h4ks.com/"}
        answer = "play at https://snake.games.h4ks.com/index.html"
        assert _guard_artifact_urls(answer, produced, set()) == answer

    def test_invented_games_link_removed(self):
        answer = "persisted at https://snake.games.h4ks.com/"
        assert "<nothing-was-uploaded>" in _guard_artifact_urls(
            answer, set(), set()
        )


def test_manifest_tag_written_by_the_model_is_stripped():
    answer = "done!\n[tools used: web_app \u2192 https://s.h4ks.com/Bb.html]"
    assert _MANIFEST_RE.sub("", answer).strip() == "done!"


class TestBackendFallback:
    cfg = {"reply_max_chars": 420, "reply_max_lines": 10}

    def drive(self, *outcomes):
        sent = []
        event = SimpleNamespace(
            nick="bob",
            chan="#chan",
            conn=None,
            bot=None,
            agent_context_urls=set(),
            reply=lambda *lines, **kw: sent.extend(lines),
        )
        with (
            patch.object(agent_plugin, "_make_run_config", return_value=None),
            patch.object(agent_plugin.Runner, "run") as run,
        ):
            run.side_effect = [
                (
                    outcome
                    if isinstance(outcome, BaseException)
                    else SimpleNamespace(final_output=outcome)
                )
                for outcome in outcomes
            ]
            err = asyncio.run(
                agent_plugin._try_backends(
                    agent=None,
                    agent_input=[{"role": "user", "content": "hi"}],
                    event=event,
                    backends_to_try=["z_ai", "openrouter"][: len(outcomes)],
                    backends_tried=[],
                    tracker=agent_plugin._RunTracker(),
                    cfg=self.cfg,
                    timeout=30,
                    max_turns=8,
                    bot=None,
                    history=deque(),
                    prompt="hi",
                )
            )
        return sent, err

    def test_refused_backend_falls_through_to_the_next(self):
        sent, err = self.drive(OpenAIError("daily limit"), "here you go")
        assert err is None
        assert sent == [
            "z_ai failed (OpenAIError), retrying on openrouter",
            "here you go",
        ]

    def test_a_refusal_comes_back_as_a_value_to_report(self):
        _, err = self.drive(OpenAIError("daily limit"))
        assert isinstance(err, OpenAIError)


def test_report_failure_always_replies():
    sent = []
    event = SimpleNamespace(reply=lambda *lines, **kw: sent.extend(lines))
    tracker = agent_plugin._RunTracker()
    tracker._calls = [("web_app", 1.0)]
    with patch.object(
        agent_plugin, "upload_markdown_paste", side_effect=OSError("no host")
    ):
        agent_plugin._report_failure(
            event, tracker, asyncio.TimeoutError(), "hi", ["z_ai"]
        )
    assert sent == ["Agent failed: [TimeoutError] 1 tool calls: web_app"]


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
