import httpx

from cloudbot.agent.tools.workflows import submit_or_share
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
    answer = submit_or_share(_client(201), "irc:mattf", "character-3d", {})

    assert "submitted job #83 for mattf" in answer
    assert "https://workflows.example/jobs/83" in answer


def test_an_unlinked_user_gets_a_form_link():
    answer = submit_or_share(_client(404), "irc:mattf", "character-3d", {})

    assert "not linked" in answer
    assert DRAFT["url"] in answer


def test_an_unidentified_user_gets_a_form_link_without_submitting():
    answer = submit_or_share(_client(500), None, "character-3d", {})

    assert "not identified" in answer
    assert DRAFT["url"] in answer
