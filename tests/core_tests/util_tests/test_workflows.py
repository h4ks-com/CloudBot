import json
from types import SimpleNamespace

import httpx
import pytest

from cloudbot.util import workflows


def _client(handler) -> workflows.WorkflowsClient:
    return workflows.WorkflowsClient(
        "https://workflows.example",
        "tok",
        transport=httpx.MockTransport(handler),
    )


def test_client_from_bot_not_configured():
    config = SimpleNamespace(get_api_key=lambda name, default=None: default)
    with pytest.raises(workflows.WorkflowsNotConfigured):
        workflows.client_from_bot(SimpleNamespace(config=config))


def test_get_json_carries_the_http_status():
    client = _client(
        lambda request: httpx.Response(404, json={"detail": "not found"})
    )
    with pytest.raises(workflows.WorkflowsError) as error:
        client.job(7)
    assert error.value.status_code == 404


def test_get_json_rejects_an_unexpected_shape():
    client = _client(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(workflows.WorkflowsError, match="unexpected response"):
        client.job(7)


def test_link_and_whois_use_identity():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = _client(handler)
    client.link("irc:acct")
    assert seen["path"] == "/api/clients/links"
    assert seen["body"] == {"identity": "irc:acct"}

    client.whois("irc:acct")
    assert seen["path"] == "/api/clients/identities/irc:acct"


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (
            httpx.Response(402, json={"detail": "not enough credits"}),
            "not enough credits",
        ),
        (httpx.Response(500), "HTTP 500"),
    ],
)
def test_error_detail_prefers_the_api_message(response, detail):
    assert workflows.error_detail(response) == detail


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
