"""Submit paid workflows.h4ks.com jobs for the person who asked."""

from typing import Any

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import workflows

NOT_LINKED = 404
NO_CREDITS = 402


def _share(
    client: workflows.WorkflowsClient,
    reason: str,
    job_type: str,
    params: dict[str, Any],
) -> str:
    resp = client.share(job_type, params)
    if resp.status_code != 201:
        return f"(error: {workflows.error_detail(resp)})"
    draft = resp.json()
    price = (
        f", {draft['quote']['credits']} credits" if draft.get("quote") else ""
    )
    return (
        f"{reason}, so give them this filled form to open, log in and submit: "
        f"{draft['url']}{price}"
    )


def submit_or_share(
    client: workflows.WorkflowsClient,
    identity: str | None,
    job_type: str,
    params: dict[str, Any],
) -> str:
    """Submit as the linked user, or fall back to a filled form link they submit themselves."""
    if identity is None:
        return _share(
            client, "they are not identified with services", job_type, params
        )
    resp = client.submit(identity, job_type, params)
    if resp.status_code == 201:
        job = resp.json()
        return (
            f"submitted job #{job['id']} for {job['owner']}, {job['quote']} credits: "
            f"{client.job_url(job['id'])}. The bot announces the start and the result in "
            "the channel itself, so never poll or wait for it."
        )
    if resp.status_code == NOT_LINKED:
        return _share(
            client,
            "their IRC account is not linked to workflows (.wf link)",
            job_type,
            params,
        )
    if resp.status_code == NO_CREDITS:
        return _share(
            client, "they do not have enough credits", job_type, params
        )
    return f"(error: {workflows.error_detail(resp)})"


@tool(
    name="workflows_submit_job",
    description=(
        "Submit a paid workflows.h4ks.com job for the person who asked, paid from their "
        "credits. Check the params schema with the workflows MCP list_job_types first. "
        "When they are not linked or cannot pay, this returns a filled form link for them "
        "instead. Use this in place of workflows_share_filled_form."
    ),
    schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "Job type name, like character-3d",
            },
            "params": {
                "type": "object",
                "description": "Form fields for the job type",
            },
        },
        "required": ["type", "params"],
    },
)
async def workflows_submit_job(ctx, data):
    event = ctx.context
    try:
        client = workflows.client_from_bot(event.bot)
    except workflows.WorkflowsNotConfigured as e:
        return f"(error: {e})"
    identity = workflows.identity_of(event.conn, event.nick)
    params = data.get("params") or {}
    try:
        return await run_in_executor(
            submit_or_share,
            client,
            identity,
            str(data.get("type") or ""),
            params,
        )
    except workflows.WorkflowsError as e:
        return f"(error: {e})"
