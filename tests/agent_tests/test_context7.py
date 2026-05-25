"""Tests for Context7 tools."""

from unittest.mock import MagicMock, patch

import pytest

from cloudbot.agent.tools.context7 import (
    DocsResponse,
    LibraryResult,
    _format_docs,
    _format_search,
    context7_docs,
    context7_search,
)


class TestFormatSearch:
    def test_empty_results(self):
        assert _format_search([]) == "No libraries found."

    def test_single_result(self):
        results: list[LibraryResult] = [
            {
                "id": "/vercel/next.js",
                "title": "Next.js",
                "description": "The React framework",
                "versions": ["v15.1.0", "v14.2.0"],
            }
        ]
        out = _format_search(results)
        assert "/vercel/next.js" in out
        assert "Next.js" in out
        assert "React framework" in out
        assert "v15.1.0" in out

    def test_no_versions(self):
        results: list[LibraryResult] = [
            {
                "id": "/some/lib",
                "title": "SomeLib",
                "description": "A thing",
            }
        ]
        out = _format_search(results)
        assert "/some/lib" in out
        assert "versions" not in out


class TestFormatDocs:
    def test_empty_data(self):
        assert _format_docs({}) == "No documentation found for this query."

    def test_code_snippet(self):
        data: DocsResponse = {
            "codeSnippets": [
                {
                    "codeTitle": "Hello",
                    "codeLanguage": "python",
                    "codeDescription": "Says hello",
                    "codeList": [
                        {"language": "python", "code": "print('hello')"}
                    ],
                }
            ]
        }
        out = _format_docs(data)
        assert "Hello" in out
        assert "python" in out
        assert "print('hello')" in out

    def test_info_snippet(self):
        data: DocsResponse = {
            "infoSnippets": [
                {
                    "breadcrumb": "Getting Started",
                    "content": "Install via npm.",
                }
            ]
        }
        out = _format_docs(data)
        assert "Getting Started" in out
        assert "Install via npm." in out

    def test_rules_included(self):
        data: DocsResponse = {
            "rules": {"content": "Always use TypeScript."},
            "codeSnippets": [],
            "infoSnippets": [],
        }
        out = _format_docs(data)
        assert "Rules" in out
        assert "TypeScript" in out

    def test_truncation(self):
        data: DocsResponse = {
            "infoSnippets": [{"content": "x" * 10000, "breadcrumb": "Big"}]
        }
        out = _format_docs(data)
        assert len(out) <= 7000
        assert "truncated" in out


class TestContext7Search:
    @pytest.mark.asyncio
    @patch("cloudbot.agent.tools.context7._get_api_key", return_value=None)
    @patch("cloudbot.agent.tools.context7.run_in_executor")
    async def test_search_success(self, mock_exec, _mock_key):
        mock_exec.return_value = [
            {
                "id": "/facebook/react",
                "title": "React",
                "description": "UI library",
                "versions": ["v18.2.0"],
            }
        ]
        ctx = MagicMock()
        ctx.context.bot = MagicMock()
        result = await context7_search(
            ctx, {"library_name": "react", "query": "state management"}
        )
        assert "/facebook/react" in result
        assert "React" in result

    @pytest.mark.asyncio
    async def test_search_missing_params(self):
        ctx = MagicMock()
        assert "error" in await context7_search(ctx, {"library_name": ""})
        assert "error" in await context7_search(ctx, {"query": ""})


class TestContext7Docs:
    @pytest.mark.asyncio
    @patch("cloudbot.agent.tools.context7._get_api_key", return_value=None)
    @patch("cloudbot.agent.tools.context7.run_in_executor")
    async def test_docs_success(self, mock_exec, _mock_key):
        mock_exec.return_value = {
            "codeSnippets": [
                {
                    "codeTitle": "useState",
                    "codeLanguage": "tsx",
                    "codeList": [
                        {
                            "language": "tsx",
                            "code": "const [x, setX] = useState(0)",
                        }
                    ],
                }
            ],
            "infoSnippets": [],
        }
        ctx = MagicMock()
        ctx.context.bot = MagicMock()
        result = await context7_docs(
            ctx,
            {"library_id": "/facebook/react", "query": "how to use useState"},
        )
        assert "useState" in result
        assert "const [x, setX] = useState(0)" in result

    @pytest.mark.asyncio
    @patch("cloudbot.agent.tools.context7._get_api_key", return_value=None)
    @patch("cloudbot.agent.tools.context7.run_in_executor")
    async def test_docs_auto_slash(self, mock_exec, _mock_key):
        mock_exec.return_value = {"codeSnippets": [], "infoSnippets": []}
        ctx = MagicMock()
        ctx.context.bot = MagicMock()
        await context7_docs(
            ctx, {"library_id": "facebook/react", "query": "hooks"}
        )
        call_args = mock_exec.call_args[0]
        assert call_args[1] == "/facebook/react"

    @pytest.mark.asyncio
    async def test_docs_missing_params(self):
        ctx = MagicMock()
        assert "error" in await context7_docs(ctx, {"library_id": ""})
        assert "error" in await context7_docs(ctx, {"query": ""})
