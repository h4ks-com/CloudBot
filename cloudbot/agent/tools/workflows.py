"""Submit paid workflows.h4ks.com jobs for the person who asked."""

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import workflows

SHARE_REASONS: dict[workflows.ShareReason, str] = {
    "unidentified": "they are not identified with services",
    "unlinked": "their IRC account is not linked to workflows (.wf link)",
    "no_credits": "they do not have enough credits",
}


def describe(outcome: workflows.Submitted | workflows.Shared) -> str:
    if isinstance(outcome, workflows.Submitted):
        return (
            f"submitted job #{outcome.job_id} for {outcome.owner}, {outcome.credits} credits: "
            f"{outcome.url}. The bot announces the start and the result in the channel itself, "
            "so never poll or wait for it."
        )
    price = (
        f", {outcome.credits} credits" if outcome.credits is not None else ""
    )
    return (
        f"{SHARE_REASONS[outcome.reason]}, so give them this filled form to open, log in "
        f"and submit: {outcome.url}{price}"
    )


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
    origin = (event.conn.name, event.chan) if event.chan else None
    try:
        outcome = await run_in_executor(
            workflows.submit_or_share,
            client,
            identity,
            str(data.get("type") or ""),
            data.get("params") or {},
            origin,
        )
    except workflows.WorkflowsError as e:
        return f"(error: {e})"
    return describe(outcome)
