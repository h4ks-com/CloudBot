import json
from unittest.mock import MagicMock

import httpx
import pytest

from cloudbot.agent.tools.workflows import workflows_submit
from cloudbot.util import workflows
from tests.util.mock_conn import account_conn


@pytest.fixture(autouse=True)
def fake_webhook(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "job_webhook",
        lambda bot, target, prefix: {"target": target, "prefix": prefix},
    )


def _ctx(handler, *, account="matt", is_private=False):
    conn = account_conn("matt", account)
    client = workflows.WorkflowsClient(
        "https://workflows.example",
        "tok",
        transport=httpx.MockTransport(handler),
    )
    bot = MagicMock()
    ctx = MagicMock()
    ctx.context.bot = bot
    ctx.context.conn = conn
    ctx.context.nick = "matt"
    ctx.context.chan = "#chan"
    ctx.context.is_private = is_private
    return ctx, client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data", [{"type": "", "params": {}}, {"type": "song", "params": "nope"}]
)
async def test_workflows_submit_rejects_bad_input(data):
    assert "error" in await workflows_submit(MagicMock(), data)


@pytest.mark.asyncio
async def test_workflows_submit_success(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/clients/jobs"
        assert body["identity"] == "irc:matt"
        assert body["webhook"] == {"target": "#chan", "prefix": "matt: "}
        return httpx.Response(
            200,
            json={"job": {"id": "j1", "quote": 111, "position": 2}},
        )

    ctx, client = _ctx(handler)
    monkeypatch.setattr(workflows, "client_from_bot", lambda bot: client)

    result = await workflows_submit(
        ctx, {"type": "song", "params": {"prompt": "hi"}}
    )
    assert "job #j1 submitted" in result
    assert "111 credits" in result


@pytest.mark.asyncio
async def test_workflows_submit_confirm_url_dms(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "job": {"id": "j1", "quote": 111},
                "confirm_url": "https://workflows.example/confirm/abc",
            },
        )

    ctx, client = _ctx(handler)
    monkeypatch.setattr(workflows, "client_from_bot", lambda bot: client)

    result = await workflows_submit(
        ctx, {"type": "song", "params": {"prompt": "hi"}}
    )
    ctx.context.notice.assert_called_once_with(
        "confirm and pay for song #j1: https://workflows.example/confirm/abc"
    )
    assert "check their DMs" in result


@pytest.mark.asyncio
async def test_workflows_submit_insufficient_balance(monkeypatch):
    def handler(request):
        return httpx.Response(402, json={"detail": "insufficient balance"})

    ctx, client = _ctx(handler)
    monkeypatch.setattr(workflows, "client_from_bot", lambda bot: client)

    result = await workflows_submit(
        ctx, {"type": "song", "params": {"prompt": "hi"}}
    )
    assert "not enough credits" in result


@pytest.mark.asyncio
async def test_workflows_submit_not_configured(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "client_from_bot",
        MagicMock(side_effect=workflows.WorkflowsNotConfigured("nope")),
    )
    ctx = MagicMock()
    result = await workflows_submit(
        ctx, {"type": "song", "params": {"prompt": "hi"}}
    )
    assert "nope" in result


@pytest.mark.asyncio
async def test_workflows_submit_reports_back_to_the_requesting_channel(
    monkeypatch,
):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"job": {"id": 1, "quote": 5}})

    ctx, client = _ctx(handler)
    monkeypatch.setattr(workflows, "client_from_bot", lambda bot: client)
    monkeypatch.setattr(workflows, "announce_channel", lambda bot: "#chan")
    await workflows_submit(ctx, {"type": "song", "params": {"prompt": "hi"}})
    assert seen["body"]["webhook"] == {"target": "#chan", "prefix": "matt: "}
