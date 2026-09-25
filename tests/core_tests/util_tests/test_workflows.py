import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from cloudbot.util import workflows


def _bot_with_keys(keys: dict[str, str]) -> SimpleNamespace:
    config = SimpleNamespace(
        get_api_key=lambda name, default=None: keys.get(name, default)
    )
    return SimpleNamespace(config=config)


def test_client_from_bot_not_configured():
    with pytest.raises(workflows.WorkflowsNotConfigured):
        workflows.client_from_bot(_bot_with_keys({}))


def test_client_from_bot_ok():
    bot = _bot_with_keys(
        {"workflows_url": "https://workflows.example", "workflows_token": "tok"}
    )
    client = workflows.client_from_bot(bot)
    assert client.base_url == "https://workflows.example"


def _client(handler) -> workflows.WorkflowsClient:
    return workflows.WorkflowsClient(
        "https://workflows.example",
        "tok",
        transport=httpx.MockTransport(handler),
    )


def test_get_json_raises_workflows_error_on_404():
    def handler(request):
        return httpx.Response(404, json={"detail": "not found"})

    client = _client(handler)
    with pytest.raises(workflows.WorkflowsError, match="HTTP 404"):
        client.job(7)


def test_get_json_rejects_an_unexpected_shape():
    client = _client(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(workflows.WorkflowsError, match="unexpected response"):
        client.job(7)


def test_submit_returns_response_for_caller_to_inspect():
    def handler(request):
        assert request.url.path == "/api/clients/jobs"
        assert json.loads(request.content) == {
            "identity": "irc:acct",
            "type": "song",
            "params": {"prompt": "hi"},
        }
        return httpx.Response(
            200,
            json={"job": {"id": "j1", "quote": 150, "position": 1}},
        )

    client = _client(handler)
    resp = client.submit("irc:acct", "song", {"prompt": "hi"})
    assert resp.status_code == 200
    assert resp.json()["job"]["id"] == "j1"


def test_submit_402_returned_not_raised():
    def handler(request):
        return httpx.Response(402, json={"detail": "insufficient balance"})

    client = _client(handler)
    resp = client.submit(None, "song", {"prompt": "hi"})
    assert resp.status_code == 402
    assert workflows.error_detail(resp) == "insufficient balance"


def test_link_and_whois_use_identity():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"link_url": "https://x/link/1"})
        return httpx.Response(200, json={"balance": 10})

    client = _client(handler)
    client.link("irc:acct")
    assert seen["path"] == "/api/clients/links"
    assert seen["body"] == {"identity": "irc:acct"}

    client.whois("irc:acct")
    assert seen["path"] == "/api/clients/identities/irc:acct"


def test_identity_for():
    assert workflows.identity_for("acct") == "irc:acct"
    assert workflows.identity_for(None) is None
    assert workflows.identity_for("") is None


def test_error_detail_falls_back_to_status():
    resp = httpx.Response(500)
    assert workflows.error_detail(resp) == "HTTP 500"


def test_notify_target_channel_highlights_nick():
    assert workflows.notify_target("matt", "#chan", False) == (
        "#chan",
        "matt: ",
    )


def test_notify_target_private_uses_dm():
    assert workflows.notify_target("matt", "matt", True) == ("matt", "")


def test_job_webhook_needs_base_url():
    bot = SimpleNamespace(config={"webhooks": {}})
    assert workflows.job_webhook(bot, "#chan", "matt: ") is None


def test_job_webhook_points_at_send_message(monkeypatch):
    monkeypatch.setattr(
        workflows, "generate_webhook_token", lambda db, expiration_hours: "tok"
    )
    monkeypatch.setattr(workflows.database, "Session", MagicMock)
    bot = SimpleNamespace(
        config={"webhooks": {"base_url": "https://bot.example/"}}
    )
    assert workflows.job_webhook(bot, "#chan", "matt: ") == {
        "url": "https://bot.example/send_message",
        "token": "tok",
        "extra_params": {"target": "#chan"},
        "message_prefix": "matt: ",
    }


def test_submit_sends_webhook():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"job": {"id": 1}})

    webhook: workflows.JobWebhook = {
        "url": "u",
        "token": "t",
        "extra_params": {"target": "#chan"},
        "message_prefix": "",
    }
    _client(handler).submit("irc:matt", "song", {"prompt": "hi"}, webhook)
    assert seen["body"]["webhook"] == webhook


def test_announce_channel_is_off_when_unset():
    assert workflows.announce_channel(SimpleNamespace(config={})) == ""


def test_subscription_comes_from_webhooks_subscriptions():
    bot = SimpleNamespace(
        config={
            "webhooks": {
                "subscriptions": [
                    {
                        "plugin": "radio",
                        "url": "https://bot/webhooks/radio",
                        "signing_key": "x",
                    },
                    {
                        "plugin": "workflows",
                        "url": "https://bot/webhooks/workflows",
                        "signing_key": "k" * 20,
                    },
                ]
            }
        }
    )
    assert workflows.subscription(bot) == {
        "url": "https://bot/webhooks/workflows",
        "signing_key": "k" * 20,
    }


def test_subscription_disabled_without_entry():
    assert (
        workflows.subscription(SimpleNamespace(config={"webhooks": {}})) is None
    )


def test_subscribe_puts_subscription():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    _client(handler).subscribe({"url": "u", "signing_key": "k" * 20})
    assert seen == {
        "method": "PUT",
        "path": "/api/clients/subscription",
        "body": {"url": "u", "signing_key": "k" * 20},
    }
