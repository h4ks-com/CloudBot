"""workflows.h4ks.com commands and channel announcements for submitted jobs."""

import logging
from typing import Any

import httpx

from cloudbot import hook
from cloudbot.util import workflows
from cloudbot.webhooks.handlers import register_webhook_handler

logger = logging.getLogger("cloudbot")


def _client(bot) -> workflows.WorkflowsClient | str:
    """Return a client or an error string to send to the channel."""
    try:
        return workflows.client_from_bot(bot)
    except workflows.WorkflowsNotConfigured as e:
        return str(e)


STATUS_LABELS = {
    "awaiting_confirmation": "waiting for confirmation",
    "queued": "in line",
    "running": "running",
    "succeeded": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}


def _bold(text: object) -> str:
    return f"\x02{text}\x02"


def _dim(text: object) -> str:
    # We end with a full reset because a bare colour close followed by digits reads as a new colour.
    return f"\x0314{text}\x0f"


def _ref(job: workflows.Job) -> str:
    return _bold(f"{job.get('type')} #{job.get('id')}")


def _minutes(seconds: int | None) -> str:
    return f"~{max(1, round((seconds or 0) / 60))} min"


def _format_job(client: workflows.WorkflowsClient, job: workflows.Job) -> str:
    status = STATUS_LABELS.get(job.get("status", ""), job.get("status", ""))
    parts = [_ref(job), status]
    if job.get("quote") is not None:
        parts.append(f"{job['quote']} credits")
    if job.get("position"):
        parts.append(f"#{job['position']} in line")
    files = (job.get("result") or {}).get("files") or []
    parts.extend(f["url"] for f in files if f.get("url"))
    if not files:
        parts.append(client.job_url(job.get("id", "")))
    return _dim(" · ").join(parts)


def _cmd_help(client: workflows.WorkflowsClient) -> list[str]:
    usage = [
        (".agi <what you want>", "run a workflow"),
        (".wf queue", "see what is running and in line"),
        (".wf status [id]", "check a job, your latest by default"),
        (".wf link", "connect your IRC account to your wallet"),
        (".wf me", "see your credits"),
        (".wf cover [seconds] [style]", "cover what just played on the radio"),
        (
            ".wf continue [seconds] [idea]",
            "continue what just played on the radio",
        ),
    ]
    return [
        f"{_bold(command)} {_dim('·')} {what}" for command, what in usage
    ] + [_dim(client.base_url)]


def _latest_job_id(
    client: workflows.WorkflowsClient, nick: str, conn
) -> str | None:
    identity = workflows.identity_of(conn, nick)
    if not identity:
        return None
    resp = client.whois(identity)
    if resp.status_code != 200:
        return None
    jobs = client.jobs(resp.json().get("username", ""), limit=1)
    return str(jobs[0]["id"]) if jobs else None


def _cmd_status(
    client: workflows.WorkflowsClient, arg: str, nick: str, conn
) -> str:
    job_id = arg.strip().split()[0] if arg.strip() else ""
    if job_id and not job_id.isdecimal():
        return "usage: .wf status [id]"
    if not job_id:
        try:
            job_id = _latest_job_id(client, nick, conn) or ""
        except workflows.WorkflowsError as e:
            return f"workflows: {e}"
        if not job_id:
            return "no jobs of yours found, use .wf status <id>"
    try:
        job = client.job(int(job_id))
    except workflows.WorkflowsError as e:
        if e.status_code == 404:
            return f"workflows: job {job_id} not found"
        return f"workflows: {e}"
    return _format_job(client, job)


def _cmd_queue(client: workflows.WorkflowsClient) -> str | list[str]:
    try:
        queue = client.queue()
    except workflows.WorkflowsError as e:
        return f"workflows: request failed: {e}"
    running = queue.get("running")
    queued = queue.get("queued") or []
    if not running and not queued:
        return "nobody in line, ask .agi to run a workflow"
    lines = []
    if running:
        owner = running.get("owner") or "someone"
        lines.append(
            f"{_bold('running')} {_ref(running)} {_dim('by')} {owner} {_dim('·')} {_minutes(running.get('eta_seconds'))} left"
        )
    for job in queued[:5]:
        owner = job.get("owner") or "someone"
        lines.append(
            f"{_dim(str(job.get('position')) + '.')} {_ref(job)} {_dim('by')} {owner} {_dim('·')} starts in {_minutes(job.get('starts_in_seconds'))}"
        )
    if len(queued) > 5:
        lines.append(_dim(f"and {len(queued) - 5} more in line"))
    return lines


def _cmd_link(
    client: workflows.WorkflowsClient, nick: str, conn, notice
) -> str:
    identity = workflows.identity_of(conn, nick)
    if not identity:
        return "identify with NickServ first, then try again"
    try:
        resp = client.link(identity)
    except workflows.WorkflowsError as e:
        return f"workflows: request failed: {e}"
    if resp.status_code >= 400:
        return f"workflows: {workflows.error_detail(resp)}"
    notice(f"link your workflows account: {resp.json().get('link_url', '')}")
    return "check your DMs to link your account"


def _cmd_me(client: workflows.WorkflowsClient, nick: str, conn, notice) -> str:
    identity = workflows.identity_of(conn, nick)
    if not identity:
        return "identify with NickServ first, then .wf link"
    try:
        resp = client.whois(identity)
    except workflows.WorkflowsError as e:
        return f"workflows: request failed: {e}"
    if resp.status_code == 404:
        return "no account linked yet, use .wf link"
    if resp.status_code >= 400:
        return f"workflows: {workflows.error_detail(resp)}"
    data = resp.json()
    free = data.get("free_credits", 0)
    paid = data.get("paid_credits", 0)
    notice(
        f"{_bold(free + paid)} credits {_dim(f'({free} free today, {paid} paid)')}"
    )
    return "check your DMs for your balance"


RADIO_CLIP_URL = "https://radio.h4ks.com/api/public/clip"
FILE_HOST_URL = "https://s.t3ks.com/api/"
DEFAULT_CLIP_SECONDS = 30
MIN_CLIP_SECONDS = 5
MAX_CLIP_SECONDS = 300
SHARE_HINTS: dict[workflows.ShareReason, str] = {
    "unidentified": "identify with NickServ and .wf link to run it from chat, or open and submit",
    "unlinked": ".wf link to run it from chat, or open and submit",
    "no_credits": "top up your credits, then open and submit",
}


def radio_clip(seconds: int) -> str:
    """Freeze the last ``seconds`` of the live radio as a public MP3 and return its URL.

    The radio serves whatever is live when asked, and a queued job fetches its song later,
    so we copy the clip to the file host right away.
    """
    try:
        clip = httpx.get(
            RADIO_CLIP_URL,
            params={"start_offset": seconds},
            timeout=workflows.TIMEOUT,
        )
        clip.raise_for_status()
        stored = httpx.post(
            FILE_HOST_URL,
            files={"file": ("radio.mp3", clip.content, "audio/mpeg")},
            timeout=workflows.TIMEOUT,
        )
        stored.raise_for_status()
    except httpx.HTTPError as e:
        raise workflows.WorkflowsError(f"could not grab the radio: {e}") from e
    url = stored.json().get("url")
    if not isinstance(url, str):
        raise workflows.WorkflowsError(
            "the file host gave no link for the clip"
        )
    return url


def _clip_request(rest: str) -> tuple[int, str]:
    words = rest.split(None, 1)
    if words and words[0].isdecimal():
        seconds = int(words[0])
        idea = words[1] if len(words) > 1 else ""
    else:
        seconds, idea = DEFAULT_CLIP_SECONDS, rest
    return max(MIN_CLIP_SECONDS, min(MAX_CLIP_SECONDS, seconds)), idea.strip()


def _cmd_radio(
    client: workflows.WorkflowsClient,
    use: str,
    rest: str,
    nick: str,
    chan: str,
    conn,
) -> str:
    seconds, idea = _clip_request(rest)
    params: dict[str, Any] = {"model": "ace-step", "use": use}
    if idea:
        params["prompt"] = idea
    try:
        params["song"] = radio_clip(seconds)
        outcome = workflows.submit_or_share(
            client,
            workflows.identity_of(conn, nick),
            "song",
            params,
            (conn.name, chan) if chan.startswith("#") else None,
        )
    except workflows.WorkflowsError as e:
        return f"workflows: {e}"
    what = f"the last {seconds}s of radio"
    if isinstance(outcome, workflows.Submitted):
        return f"follow your {use} of {what} {_dim('·')} {outcome.credits} credits {_dim('·')} {outcome.url}"
    return f"{SHARE_HINTS[outcome.reason]} {_dim('·')} {outcome.url}"


@hook.command("wf", "workflows", autohelp=False)
def wf_cmd(text, nick, chan, conn, bot, notice, event):
    """<queue|status|me|link|cover|continue> [id|seconds] [idea] - follow workflows or cover the radio"""
    client = _client(bot)
    if isinstance(client, str):
        return client

    parts = text.strip().split(None, 1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1] if len(parts) > 1 else ""

    match sub:
        case "status":
            return _cmd_status(client, rest, nick, conn)
        case "queue":
            return _cmd_queue(client)
        case "link":
            return _cmd_link(client, nick, conn, notice)
        case "me":
            return _cmd_me(client, nick, conn, notice)
        case "cover" | "continue":
            return _cmd_radio(client, sub, rest, nick, chan, conn)
        case _:
            return _cmd_help(client)


def format_event(payload: dict[str, Any]) -> str | None:
    """IRC line for a job event from the service, or None for statuses we stay quiet about."""
    to_owner = f"{payload['owner']}: " if payload.get("owner") else ""
    ref = _bold(
        f"{str(payload.get('type_title') or payload.get('type')).lower()} #{payload.get('job_id')}"
    )
    run_url = payload.get("run_url", "")
    match payload.get("status"):
        case "queued":
            return f"{to_owner}{ref} is in line {_dim('·')} {run_url}"
        case "running":
            return f"{to_owner}{ref} started"
        case "succeeded":
            title = (
                f" {_bold(payload['title'])}" if payload.get("title") else ""
            )
            urls = " ".join(payload.get("result_urls") or []) or run_url
            return f"{to_owner}{ref} is done:{title} {_dim('·')} {urls}"
        case "failed":
            return f"{to_owner}{ref} failed: {payload.get('error') or 'unknown error'} {_dim('·')} {run_url}"
        case "cancelled":
            return f"{to_owner}{ref} was cancelled"
    return None


def _targets(bot, payload: dict[str, Any]) -> set[tuple[str, str]]:
    targets = set()
    if channel := workflows.announce_channel(bot):
        targets.add((workflows.announce_connection(bot), channel))
    job_id = payload.get("job_id")
    if isinstance(job_id, int):
        origin = workflows.origin_of(job_id, str(payload.get("status")))
        if origin:
            targets.add(origin)
    return targets


def handle_workflows_event(bot, payload: dict[str, Any]) -> None:
    message = format_event(payload)
    if not message:
        return
    for connection_name, channel in _targets(bot, payload):
        connection = bot.connections.get(connection_name)
        if not connection or not connection.connected:
            logger.warning(
                "workflows: connection %s is not available", connection_name
            )
            continue
        connection.message(channel, message)


@hook.on_start()
def subscribe_workflows(bot):
    """Register the job event handler and subscribe to every workflows job event."""
    register_webhook_handler("workflows", handle_workflows_event)
    subscription = workflows.subscription(bot)
    if subscription is None:
        return
    try:
        client = workflows.client_from_bot(bot)
        resp = client.subscribe(subscription)
    except workflows.WorkflowsError as e:
        logger.error("workflows subscription failed: %s", e)
        return
    if resp.status_code >= 400:
        logger.error(
            "workflows subscription failed: %s", workflows.error_detail(resp)
        )
