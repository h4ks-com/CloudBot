import httpx
import pytest

from cloudbot.agent.tools.workflows import describe
from cloudbot.util import workflows

JOB = {"id": 83, "owner": "mattf", "quote": 500}
DRAFT = {"url": "https://workflows.example/d/abc", "quote": {"credits": 500}}


def _client(submit_status: int) -> workflows.WorkflowsClient:
    def handler(request):
        if request.url.path == "/api/clients/jobs":
            return httpx.Response(
                submit_status,
                json=JOB if submit_status == 201 else {"detail": "no"},
            )
        return httpx.Response(201, json=DRAFT)

    return workflows.WorkflowsClient(
        "https://workflows.example",
        "tok",
        transport=httpx.MockTransport(handler),
    )


def test_a_linked_user_gets_a_submitted_job():
    outcome = workflows.submit_or_share(
        _client(201), "irc:mattf", "character-3d", {}, ("gobot", "#lobby")
    )

    assert outcome == workflows.Submitted(
        83, "mattf", 500, "https://workflows.example/jobs/83"
    )
    assert workflows.origin_of(83, "running") == ("gobot", "#lobby")
    assert "submitted job #83 for mattf" in describe(outcome)


def test_an_unlinked_user_gets_a_form_link():
    outcome = workflows.submit_or_share(
        _client(404), "irc:mattf", "character-3d", {}
    )

    assert outcome == workflows.Shared("unlinked", DRAFT["url"], 500)
    assert "not linked" in describe(outcome)


def test_an_unidentified_user_gets_a_form_link_without_submitting():
    outcome = workflows.submit_or_share(_client(500), None, "character-3d", {})

    assert outcome == workflows.Shared("unidentified", DRAFT["url"], 500)


def test_a_refused_job_raises_the_service_reason():
    with pytest.raises(workflows.WorkflowsError, match="no"):
        workflows.submit_or_share(_client(422), "irc:mattf", "character-3d", {})
