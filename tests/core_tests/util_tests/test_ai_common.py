import json
import re
from unittest.mock import patch

from cloudbot.util.ai_common import (
    Message,
    _js_safe_json,
    _safe_content,
    format_reply_lines,
    upload_history,
    wrap_reply_lines,
)

# ---------------------------------------------------------------------------
# _safe_content
# ---------------------------------------------------------------------------


class TestSafeContent:
    def test_plain_text_unchanged(self):
        assert _safe_content("hello world") == "hello world"

    def test_markdown_unchanged(self):
        md = "# Title\n\nSome **bold** text and a [link](https://example.com)"
        assert _safe_content(md) == md

    def test_partial_html_unchanged(self):
        # Snippets that aren't a full document are left for DOMPurify client-side
        assert _safe_content("<div>foo</div>") == "<div>foo</div>"
        assert _safe_content("<style>body{background:red}</style>") == (
            "<style>body{background:red}</style>"
        )

    def test_empty_unchanged(self):
        assert _safe_content("") == ""

    def test_doctype_wrapped(self):
        html = "<!DOCTYPE html>\n<html><body>hi</body></html>"
        result = _safe_content(html)
        assert result == f"```html\n{html}\n```"

    def test_doctype_lowercase_wrapped(self):
        html = "<!doctype html>\n<html><body>hi</body></html>"
        result = _safe_content(html)
        assert result.startswith("```html\n")
        assert html in result

    def test_doctype_mixedcase_wrapped(self):
        html = "<!Doctype HTML>\n<html><body>hi</body></html>"
        result = _safe_content(html)
        assert result.startswith("```html\n")
        assert html in result

    def test_html_tag_wrapped(self):
        html = "<html><head></head><body>content</body></html>"
        result = _safe_content(html)
        assert result == f"```html\n{html}\n```"

    def test_html_tag_leading_whitespace_wrapped(self):
        html = "  \n\t<!DOCTYPE html>\n<html><body>hi</body></html>"
        result = _safe_content(html)
        assert result.startswith("```html\n")
        assert html in result

    def test_wrapped_content_is_inert_code_fence(self):
        # The output is a markdown code fence. Marked.js always escapes content
        # inside fences, so the HTML tags can never reach the page DOM.
        html = (
            "<!DOCTYPE html>\n<html>\n<head>\n"
            "<style>body{background:parchment}</style>\n"
            "</head>\n<body>\n<script>alert(1)</script>\n</body>\n</html>"
        )
        result = _safe_content(html)
        assert result == f"```html\n{html}\n```"

    def test_wrapping_real_world_ai_response(self):
        # Mirrors the actual GEQ.html bot responses that bled into the page.
        html = (
            '<!doctype html>\n<html lang="en">\n<head>\n'
            '<meta charset="utf-8" />\n'
            "<style>html,body{height:100%;margin:0;background:#ddd7cc}</style>\n"
            "</head>\n<body>\n"
            '<div class="controls"><button id="prevBtn">Previous</button></div>\n'
            "</body>\n</html>"
        )
        result = _safe_content(html)
        assert result.startswith("```html\n")
        assert result.endswith("\n```")
        assert html in result


# ---------------------------------------------------------------------------
# _js_safe_json
# ---------------------------------------------------------------------------


class TestJsSafeJson:
    def test_plain_string(self):
        assert _js_safe_json("hello") == '"hello"'

    def test_no_script_tag_unchanged(self):
        data = {"key": "value"}
        result = _js_safe_json(data)
        assert json.loads(result) == data

    def test_script_closing_tag_escaped(self):
        # </script> inside JSON would terminate the <script> block in HTML
        payload = {"content": "foo</script><script>alert(1)</script>"}
        result = _js_safe_json(payload)
        assert "</script>" not in result
        assert "<\\/script>" in result

    def test_all_closing_slash_tags_escaped(self):
        # We escape ALL </ sequences, not just </script>
        payload = "test</div></style></script>"
        result = _js_safe_json(payload)
        assert "</" not in result
        assert result.count("<\\/") == 3

    def test_escaped_value_round_trips(self):
        # After reversing the escaping, json.loads must recover the original value.
        # (<\/ is transparent to a JS engine but not to Python's json.loads,
        # so we reverse it before parsing.)
        payload = {"msg": "end</script>more</script>"}
        safe = _js_safe_json(payload)
        recovered = json.loads(safe.replace("<\\/", "</"))
        assert recovered == payload

    def test_nested_structure(self):
        msgs = [
            {"role": "user", "content": "hello</script>"},
            {"role": "assistant", "content": "<!doctype html></html>"},
        ]
        result = _js_safe_json(msgs)
        assert "</script>" not in result
        assert "</html>" not in result


# ---------------------------------------------------------------------------
# upload_history integration
# ---------------------------------------------------------------------------


def _extract_msgs(page: str) -> list[dict]:
    """Parse the MSGS array out of the generated HTML page."""
    match = re.search(r"const MSGS=(.+?);\s*marked", page, re.DOTALL)
    assert match, "MSGS variable not found in page"
    # Reverse _js_safe_json escaping so json.loads can parse it
    return json.loads(match.group(1).replace("<\\/", "</"))


def _fake_paste():
    captured = {}

    def paste(data, ext=None):
        captured["data"] = data
        return "https://s.h4ks.com/test.html"

    return paste, captured


class TestUploadHistory:
    def _make_messages(self, *pairs):
        return [Message(role=role, content=content) for role, content in pairs]

    def test_html_doc_becomes_code_fence_in_msgs(self):
        html_response = (
            "<!DOCTYPE html>\n<html><head>"
            "<style>body{background:parchment}</style>"
            "</head><body><div>book app</div></body></html>"
        )
        messages = self._make_messages(
            ("user", "make a book app"),
            ("assistant", html_response),
        )
        paste, captured = _fake_paste()
        with patch("cloudbot.util.ai_common.web.paste", side_effect=paste):
            result = upload_history("testnick", messages, "Test conversation")

        assert result == "https://s.h4ks.com/test.html"
        page = captured["data"].decode("utf-8")

        msgs = _extract_msgs(page)
        bot_msg = next(m for m in msgs if m["role"] == "assistant")

        # Content must be wrapped in a code fence
        assert bot_msg["content"].startswith("```html\n")
        assert bot_msg["content"].endswith("\n```")
        # Original HTML is preserved inside the fence
        assert "<style>body{background:parchment}</style>" in bot_msg["content"]
        assert "<div>book app</div>" in bot_msg["content"]

    def test_script_tag_cannot_break_script_block(self):
        # </script> in message content must be escaped so it doesn't terminate
        # the page's <script> block and expose the JS source as raw text.
        messages = self._make_messages(
            ("user", "normal message"),
            ("assistant", "here is code: </script><script>alert(1)</script>"),
        )
        paste, captured = _fake_paste()
        with patch("cloudbot.util.ai_common.web.paste", side_effect=paste):
            upload_history("nick", messages, "header")

        page = captured["data"].decode("utf-8")
        # Extract just the MSGS JSON section — the template's own </script>
        # closing tag is fine; only injected ones matter
        match = re.search(r"const MSGS=(.+?);\s*marked", page, re.DOTALL)
        msgs_json_raw = match.group(1)
        assert "</script>" not in msgs_json_raw

    def test_real_world_bleed_scenario(self):
        # The GEQ.html scenario: raw multi-thousand-char HTML responses that
        # leaked their <style> into the page and made all bubbles look identical.
        html_response = (
            '<!doctype html>\n<html lang="en">\n<head>\n'
            "<style>html,body{height:100%;background:#ddd7cc}</style>\n"
            "</head>\n<body>\n"
            '<div class="controls"><button>Previous</button></div>\n'
            "</body>\n</html>"
        )
        messages = self._make_messages(
            ("user", "make a book"),
            ("assistant", html_response),
            ("user", "fix it"),
            ("assistant", html_response.replace("Previous", "Next")),
        )
        paste, captured = _fake_paste()
        with patch("cloudbot.util.ai_common.web.paste", side_effect=paste):
            upload_history("nick", messages, "header")

        msgs = _extract_msgs(captured["data"].decode("utf-8"))
        bot_msgs = [m for m in msgs if m["role"] == "assistant"]

        # Both bot responses must be fenced code blocks
        for msg in bot_msgs:
            assert msg["content"].startswith("```html\n")
        # And they must be distinct (not collapsed to the same content)
        assert bot_msgs[0]["content"] != bot_msgs[1]["content"]

    def test_title_appears_in_page(self):
        messages = self._make_messages(("user", "hi"), ("assistant", "hello"))
        paste, captured = _fake_paste()
        with patch("cloudbot.util.ai_common.web.paste", side_effect=paste):
            upload_history("nick", messages, "nick's GPT conversation in #dev")

        page = captured["data"].decode("utf-8")
        assert "nick's GPT conversation in #dev" in page

    def test_title_html_is_escaped_in_title_tag(self):
        # If header somehow contains HTML characters they must be escaped in
        # the <title> tag — a </title><script> would otherwise break out.
        messages = self._make_messages(("user", "hi"), ("assistant", "hi"))
        paste, captured = _fake_paste()
        malicious_header = "x</title><script>alert(1)</script><title>y"
        with patch("cloudbot.util.ai_common.web.paste", side_effect=paste):
            upload_history("nick", messages, malicious_header)

        page = captured["data"].decode("utf-8")
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_regular_markdown_not_wrapped(self):
        messages = self._make_messages(
            ("user", "what is blue?"),
            (
                "assistant",
                "Blue is a **color** with wavelength ~450nm.\n\n- short\n- wave",
            ),
        )
        paste, captured = _fake_paste()
        with patch("cloudbot.util.ai_common.web.paste", side_effect=paste):
            upload_history("nick", messages, "header")

        msgs = _extract_msgs(captured["data"].decode("utf-8"))
        bot_msg = next(m for m in msgs if m["role"] == "assistant")
        assert not bot_msg["content"].startswith("```html")


# ---------------------------------------------------------------------------
# format_reply_lines
# ---------------------------------------------------------------------------


class TestFormatReplyLines:
    def test_keeps_lines_within_budget(self):
        assert format_reply_lines("a\nb\nc") == ["a", "b", "c"]

    def test_single_line(self):
        assert format_reply_lines("just one line") == ["just one line"]

    def test_empty(self):
        assert format_reply_lines("   ") == []
        assert format_reply_lines("") == []

    def test_drops_blank_lines(self):
        assert format_reply_lines("a\n\n  \nb") == ["a", "b"]

    def test_long_line_split_into_byte_chunks(self):
        line = "x" * 50
        result = format_reply_lines(line, max_line_bytes=20)
        assert len(result) == 3
        assert all(len(piece.encode("utf-8")) <= 20 for piece in result)
        assert "".join(result) == line

    def test_overflow_leads_with_paste_link(self):
        text = "\n".join(f"line {i}" for i in range(20))
        result = format_reply_lines(
            text, max_lines=5, paste=lambda: "http://p/x"
        )
        assert result[0] == "full: http://p/x"
        assert result[1:] == [f"line {i}" for i in range(5)]

    def test_overflow_without_paste_just_truncates(self):
        text = "\n".join(f"line {i}" for i in range(20))
        result = format_reply_lines(text, max_lines=5)
        assert result == [f"line {i}" for i in range(5)]

    def test_paste_failure_falls_back_to_truncation(self):
        def boom():
            raise ValueError("paste down")

        text = "\n".join(f"line {i}" for i in range(20))
        result = format_reply_lines(text, max_lines=3, paste=boom)
        assert result == ["line 0", "line 1", "line 2"]


class TestWrapReplyLines:
    def test_within_budget_no_url(self):
        assert wrap_reply_lines("a\nb") == (["a", "b"], None)

    def test_overflow_returns_lines_and_url(self):
        text = "\n".join(f"line {i}" for i in range(20))
        lines, url = wrap_reply_lines(
            text, max_lines=4, paste=lambda: "http://p/x"
        )
        assert lines == [f"line {i}" for i in range(4)]
        assert url == "http://p/x"

    def test_empty(self):
        assert wrap_reply_lines("  ") == ([], None)
