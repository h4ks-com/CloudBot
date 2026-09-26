import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from cloudbot.util import workflows
from cloudbot.util.colors import strip_irc
from plugins import workflows as wf_plugin
from tests.util.mock_conn import account_conn


def _mock_client(handler) -> workflows.WorkflowsClient:
    return workflows.WorkflowsClient(
        "https://workflows.example",
        "tok",
        transport=httpx.MockTransport(handler),
    )


def _wf(monkeypatch, client, **overrides):
    monkeypatch.setattr(wf_plugin, "_client", lambda bot: client)
    kwargs = {
        "nick": "matt",
        "chan": "#chan",
        "conn": account_conn("matt", "matt"),
        "bot": MagicMock(),
        "notice": lambda *a, **kw: None,
        "event": SimpleNamespace(is_private=False),
    }
    kwargs.update(overrides)
    return kwargs


def test_wf_help_lists_commands_only(monkeypatch):
    client = _mock_client(lambda request: httpx.Response(500))
    result = [
        strip_irc(line)
        for line in wf_plugin.wf_cmd("", **_wf(monkeypatch, client))
    ]
    assert any(line.startswith(".agi <what you want>") for line in result)
    assert not any("song" in line for line in result)


def test_wf_status_not_found(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"detail": "not found"})

    client = _mock_client(handler)
    result = wf_plugin.wf_cmd("status 7", **_wf(monkeypatch, client))
    assert "job 7 not found" in result


def test_wf_status_formats_job(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": 7,
                "type": "song",
                "status": "running",
                "quote": 111,
                "position": 0,
            },
        )

    client = _mock_client(handler)
    result = wf_plugin.wf_cmd("status 7", **_wf(monkeypatch, client))
    assert "song #7 · running · 111 credits" in strip_irc(result)
    assert f"{client.base_url}/jobs/7" in result


@pytest.mark.parametrize("job_id", ["../clients/identities/irc:bob", "²"])
def test_wf_status_rejects_non_numeric_ids(monkeypatch, job_id):
    client = _mock_client(lambda request: httpx.Response(500))
    result = wf_plugin.wf_cmd(f"status {job_id}", **_wf(monkeypatch, client))
    assert result == "usage: .wf status [id]"


def test_wf_link_requires_services_account(monkeypatch):
    client = _mock_client(lambda request: httpx.Response(200, json=[]))
    result = wf_plugin.wf_cmd(
        "link", **_wf(monkeypatch, client, conn=account_conn("matt", None))
    )
    assert "identify with NickServ" in result


def test_wf_link_dms_link_url(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, json={"link_url": "https://workflows.example/link/abc"}
        )

    client = _mock_client(handler)
    notices: list[str] = []
    result = wf_plugin.wf_cmd(
        "link", **_wf(monkeypatch, client, notice=notices.append)
    )
    assert notices == [
        "link your workflows account: https://workflows.example/link/abc"
    ]
    assert "check your DMs" in result


def test_wf_me_dms_balance(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={"username": "matt", "free_credits": 200, "paid_credits": 50},
        )

    client = _mock_client(handler)
    notices: list[str] = []
    result = wf_plugin.wf_cmd(
        "me", **_wf(monkeypatch, client, notice=notices.append)
    )
    assert [strip_irc(n) for n in notices] == [
        "250 credits (200 free today, 50 paid)"
    ]
    assert "check your DMs" in result


def test_wf_not_configured(monkeypatch):
    monkeypatch.setattr(
        wf_plugin.workflows,
        "client_from_bot",
        MagicMock(
            side_effect=wf_plugin.workflows.WorkflowsNotConfigured("nope")
        ),
    )
    result = wf_plugin.wf_cmd(
        "",
        nick="matt",
        chan="#chan",
        conn=account_conn("matt", "matt"),
        bot=MagicMock(),
        notice=lambda *a, **kw: None,
        event=SimpleNamespace(is_private=False),
    )
    assert result == "nope"


def test_subscribe_workflows_on_start(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = _mock_client(handler)
    monkeypatch.setattr(workflows, "client_from_bot", lambda bot: client)
    monkeypatch.setattr(
        workflows, "subscription", lambda bot: {"url": "u", "token": "t"}
    )
    wf_plugin.subscribe_workflows(MagicMock())
    assert seen["body"] == {"url": "u", "token": "t"}


EVENT = {
    "event": "job",
    "job_id": 12,
    "type": "image",
    "type_title": "Image",
    "owner": "mattf",
    "title": "Neon Rain",
    "run_url": "https://w/jobs/12",
    "result_urls": ["https://s3/x.png"],
    "error": None,
}


@pytest.mark.parametrize(
    ("status", "text"),
    [
        ("queued", "mattf: image #12 is in line · https://w/jobs/12"),
        ("running", "mattf: image #12 started"),
        (
            "succeeded",
            "mattf: image #12 is done: Neon Rain · https://s3/x.png",
        ),
        (
            "failed",
            "mattf: image #12 failed: unknown error · https://w/jobs/12",
        ),
        ("cancelled", "mattf: image #12 was cancelled"),
    ],
)
def test_format_event(status, text):
    assert (
        strip_irc(wf_plugin.format_event({**EVENT, "status": status})) == text
    )


def test_format_event_ignores_unknown_status():
    assert (
        wf_plugin.format_event({**EVENT, "status": "awaiting_confirmation"})
        is None
    )


def test_handler_posts_to_the_announce_channel():
    sent = []
    conn = SimpleNamespace(
        connected=True, message=lambda chan, msg: sent.append((chan, msg))
    )
    bot = SimpleNamespace(
        config={
            "plugins": {
                "workflows": {
                    "announce_channel": "#lobby",
                    "connection": "gobot",
                }
            }
        },
        connections={"gobot": conn},
    )
    wf_plugin.handle_workflows_event(bot, {**EVENT, "status": "running"})
    assert [(chan, strip_irc(msg)) for chan, msg in sent] == [
        ("#lobby", "mattf: image #12 started")
    ]


def test_wf_status_shows_result_files_and_defaults_to_latest(monkeypatch):
    done = {
        "id": 7,
        "type": "image",
        "status": "succeeded",
        "quote": 40,
        "result": {"files": [{"url": "https://s3/workflows/image/x.png"}]},
    }

    def handler(request):
        if request.url.path.startswith("/api/clients/identities/"):
            return httpx.Response(200, json={"username": "matt"})
        if (
            request.url.path == "/api/jobs"
            and request.url.params["user"] == "matt"
        ):
            return httpx.Response(200, json=[done])
        if request.url.path == "/api/jobs/7":
            return httpx.Response(200, json=done)
        raise AssertionError(request.url)

    client = _mock_client(handler)
    result = strip_irc(wf_plugin.wf_cmd("status", **_wf(monkeypatch, client)))
    assert (
        result
        == "image #7 · done · 40 credits · https://s3/workflows/image/x.png"
    )
