"""Client for the workflows.h4ks.com API, used by the .wf plugin.

The service knows each caller by an opaque identity (``irc:<services account>``) and signs every
job event it posts to ``/webhooks/workflows`` for the subscription in ``webhooks.subscriptions``.
Config: ``api_keys.workflows_url`` and ``api_keys.workflows_token``.
"""

from functools import lru_cache
from typing import Any, TypedDict, TypeVar, cast
from urllib.parse import quote

import httpx

from plugins.core.chan_track import get_users

TIMEOUT = 30.0

JsonShape = TypeVar("JsonShape", dict, list)


class Subscription(TypedDict):
    url: str
    signing_key: str


class JobResult(TypedDict, total=False):
    files: list[dict[str, str]]
    title: str


class Job(TypedDict, total=False):
    id: int
    type: str
    status: str
    owner: str | None
    quote: int
    position: int | None
    eta_seconds: int | None
    starts_in_seconds: int | None
    result: JobResult | None


class Queue(TypedDict, total=False):
    running: Job | None
    queued: list[Job]


class WorkflowsError(Exception):
    """Any failure talking to the workflows API, with the HTTP status when there was one."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class WorkflowsNotConfigured(WorkflowsError):
    """api_keys.workflows_url or workflows_token is missing from config."""


class WorkflowsClient:
    """Thin wrapper over the workflows API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=TIMEOUT,
            transport=transport,
        )

    def _get_json(self, path: str, shape: type[JsonShape]) -> JsonShape:
        try:
            resp = self.http.get(path)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as e:
            raise WorkflowsError(
                f"HTTP {e.response.status_code}: {error_detail(e.response)}",
                e.response.status_code,
            ) from e
        except (httpx.HTTPError, ValueError) as e:
            raise WorkflowsError(str(e)) from e
        if not isinstance(body, shape):
            raise WorkflowsError(f"unexpected response from {path}")
        return body

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self.http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise WorkflowsError(str(e)) from e

    def queue(self) -> Queue:
        return cast(Queue, self._get_json("/api/queue", dict))

    def job(self, job_id: int) -> Job:
        return cast(Job, self._get_json(f"/api/jobs/{job_id}", dict))

    def jobs(self, user: str, limit: int = 1) -> list[Job]:
        return self._get_json(
            f"/api/jobs?user={quote(user)}&limit={limit}", list
        )

    def job_url(self, job_id: int | str) -> str:
        return f"{self.base_url}/jobs/{job_id}"

    def subscribe(self, subscription: Subscription) -> httpx.Response:
        return self._request(
            "PUT", "/api/clients/subscription", json=subscription
        )

    def link(self, identity: str) -> httpx.Response:
        return self._request(
            "POST", "/api/clients/links", json={"identity": identity}
        )

    def whois(self, identity: str) -> httpx.Response:
        return self._request(
            "GET", f"/api/clients/identities/{quote(identity, safe='')}"
        )


def identity_for(irc_account: str | None) -> str | None:
    """The opaque identity string the service expects, or None if unidentified."""
    return f"irc:{irc_account}" if irc_account else None


def identity_of(conn: Any, nick: str) -> str | None:
    """The identity of ``nick``'s services account on ``conn``, or None when they are not identified."""
    return identity_for(get_users(conn).getuser(nick).account)


@lru_cache(maxsize=4)
def _shared_client(url: str, token: str) -> WorkflowsClient:
    return WorkflowsClient(url, token)


def client_from_bot(bot: Any) -> WorkflowsClient:
    """The shared client for ``api_keys.workflows_url``/``workflows_token``, or raise."""
    url = bot.config.get_api_key("workflows_url")
    token = bot.config.get_api_key("workflows_token")
    if not (url and token):
        raise WorkflowsNotConfigured(
            "workflows is not configured, set api_keys.workflows_url and workflows_token"
        )
    return _shared_client(url, token)


def error_detail(resp: httpx.Response) -> str:
    """The API's own error message, falling back to the raw body or status."""
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail or resp.text[:200] or f"HTTP {resp.status_code}"


def announce_channel(bot: Any) -> str:
    """Channel that sees every job, from ``plugins.workflows.announce_channel``; unset disables it."""
    return str(
        bot.config.get("plugins", {})
        .get("workflows", {})
        .get("announce_channel", "")
    )


def announce_connection(bot: Any) -> str:
    """Connection the announce channel is on, from ``plugins.workflows.connection``."""
    return str(
        bot.config.get("plugins", {}).get("workflows", {}).get("connection", "")
    )


def subscription(bot: Any) -> Subscription | None:
    """Our ``webhooks.subscriptions`` entry for plugin ``workflows``, as the service expects it."""
    for sub in bot.config.get("webhooks", {}).get("subscriptions", []):
        if (
            sub.get("plugin") == "workflows"
            and sub.get("url")
            and sub.get("signing_key")
        ):
            return {"url": sub["url"], "signing_key": sub["signing_key"]}
    return None
