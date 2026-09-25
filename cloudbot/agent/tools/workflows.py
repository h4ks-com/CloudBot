"""Agent tool to submit a workflows.h4ks.com job for the requesting user.

Browsing (types, queue, jobs) goes through the service's public MCP server.
"""

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import workflows


@tool(
    name="workflows_submit",
    description=(
        "Submit a paid job to workflows.h4ks.com "
        "on behalf of the user talking to you, billed in credits. List job types and their "
        "params schema first with the workflows MCP server's types tool. Params must be an "
        "object matching that schema. "
        "Returns the job id, price in credits and the run page URL. The bot "
        "posts the job's start and result to the user itself, so never poll or wait "
        "for it. If the job needs the user to confirm and pay, a confirm link is DMed to them "
        "directly; tell them to check their DMs and never invent or repeat the link yourself."
    ),
    schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "Workflow name from the workflows MCP server's job types",
            },
            "params": {
                "type": "object",
                "description": "Job parameters matching the type's params schema",
            },
        },
        "required": ["type", "params"],
    },
)
async def workflows_submit(ctx, data):
    type_name = str(data.get("type") or "").strip()
    if not type_name:
        return "(error: type required)"
    params = data.get("params")
    if not isinstance(params, dict):
        return "(error: params must be an object)"

    event = ctx.context
    try:
        client = workflows.client_from_bot(event.bot)
    except workflows.WorkflowsNotConfigured as e:
        return f"(error: {e})"

    identity = workflows.identity_of(event.conn, event.nick)
    target, prefix = workflows.notify_target(
        event.nick, event.chan, event.is_private
    )
    webhook = await run_in_executor(
        workflows.job_webhook, event.bot, target, prefix
    )

    try:
        resp = await run_in_executor(
            client.submit,
            identity,
            type_name,
            params,
            webhook,
        )
    except workflows.WorkflowsError as e:
        return f"(error: {e})"

    if resp.status_code == 402:
        return (
            f"(error: not enough credits, top up at {client.base_url}/wallet)"
        )
    if resp.status_code >= 400:
        return f"(error: {workflows.error_detail(resp)})"

    body = resp.json()
    job = body.get("job") or {}
    job_id = job.get("id")
    confirm_url = body.get("confirm_url")

    if confirm_url:
        event.notice(
            f"confirm and pay for {type_name} #{job_id}: {confirm_url}"
        )
        return (
            f"job #{job_id} costs {job.get('quote')} credits; "
            "a confirm link was DMed to the user; tell them to check their DMs"
        )
    return f"job #{job_id} submitted, {job.get('quote')} credits: {client.job_url(job_id)}"
