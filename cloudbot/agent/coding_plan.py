"""GLM Coding Plan usage from z.ai's own monitor endpoint.

One GET returns the plan's global usage meters: the 5-hour session window,
the weekly window when z.ai reports one, and the monthly web-search allowance.
These are the same endpoints the z.ai subscription UI uses; response shapes
are stable in practice.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from cloudbot.util import colors
from cloudbot.util.web import get_session

logger = logging.getLogger("cloudbot")

QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
_BAR_WIDTH = 14


class CodingPlanError(Exception):
    """The usage lookup failed; str(e) is chat-ready."""


@dataclass
class PlanUsage:
    """Meters as z.ai reports them. Percentage meters carry only their
    percentage; the web-search meter carries used/limit counts."""

    level: str
    session_pct: int | None = None
    session_resets_at: float | None = None
    week_pct: int | None = None
    week_resets_at: float | None = None
    web_used: int | None = None
    web_limit: int | None = None
    web_resets_at: float | None = None


def fetch(api_key: str) -> PlanUsage:
    if not api_key:
        raise CodingPlanError("z.ai API key not configured")
    try:
        resp = get_session().get(
            QUOTA_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
    except requests.RequestException as e:
        raise CodingPlanError(f"usage lookup failed: {e}") from e
    if resp.status_code == 401:
        raise CodingPlanError("z.ai API key rejected")
    if resp.status_code != 200:
        raise CodingPlanError(f"usage lookup returned HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as e:
        raise CodingPlanError("usage lookup returned invalid JSON") from e
    if body.get("success") is False:
        raise CodingPlanError(
            "no GLM Coding Plan on this key: " + str(body.get("msg") or "")
        )
    limits = ((body.get("data") or {}).get("limits")) or []
    usage = PlanUsage(level=str((body.get("data") or {}).get("level") or "?"))
    for entry in limits:
        kind = entry.get("type") or entry.get("name")
        unit = entry.get("unit")
        number = entry.get("number") or 0
        days = {"3": 1 / 24, "4": 1, "5": 30, "6": 7}.get(str(unit), 0) * number
        if kind == "TOKENS_LIMIT" and days < 1:
            usage.session_pct = entry.get("percentage")
            usage.session_resets_at = _epoch_s(entry.get("nextResetTime"))
        elif kind == "TOKENS_LIMIT":
            usage.week_pct = entry.get("percentage")
            usage.week_resets_at = _epoch_s(entry.get("nextResetTime"))
        elif kind == "TIME_LIMIT":
            usage.web_used = entry.get("currentValue")
            usage.web_limit = entry.get("usage")
            usage.web_resets_at = _epoch_s(entry.get("nextResetTime"))
    return usage


def _epoch_s(value) -> float | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return value / 1000


def _usage_color(fraction: float) -> str:
    if fraction >= 0.8:
        return "red"
    if fraction >= 0.5:
        return "yellow"
    return "green"


def _bar(fraction: float) -> str:
    color = _usage_color(fraction)
    filled = round(min(1.0, max(0.0, fraction)) * _BAR_WIDTH)
    return str(
        colors.parse(
            f"$({color}){'█' * filled}"
            f"$(dgrey){'░' * (_BAR_WIDTH - filled)}$(clear)"
        )
    )


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60:02d}m"
    return f"{minutes}m"


def _remaining(resets_at: float | None) -> str:
    if resets_at is None:
        return ""
    left = resets_at - datetime.now(timezone.utc).timestamp()
    if left <= 0:
        return ""
    return str(colors.parse(f" $(dgrey)· resets in {_duration(left)}$(clear)"))


def _pct_line(label: str, pct: int | None, resets_at: float | None) -> str:
    fraction = min(1.0, max(0.0, (pct or 0) / 100))
    color = _usage_color(fraction)
    return str(
        colors.parse(
            f"$(bold){label:<5}$(clear) {_bar(fraction)} "
            f"$({color}){pct if pct is not None else '?'}%$(clear)"
            + _remaining(resets_at)
        )
    )


def _web_line(usage: PlanUsage) -> str:
    limit = usage.web_limit or 0
    used = usage.web_used or 0
    fraction = min(1.0, used / limit) if limit else 0
    color = _usage_color(fraction)
    pct = f"{fraction * 100:.0f}%"
    return str(
        colors.parse(
            f"$(bold)web:  $(clear) {_bar(fraction)} $({color}){pct}$(clear) "
            f"({used:,}/{limit:,} searches)" + _remaining(usage.web_resets_at)
        )
    )


def render(usage: PlanUsage) -> list[str]:
    lines = [
        colors.parse(f"GLM Coding Plan $(bold){usage.level}$(clear)"),
        _pct_line("5h:", usage.session_pct, usage.session_resets_at),
    ]
    if usage.week_pct is not None:
        lines.append(_pct_line("week:", usage.week_pct, usage.week_resets_at))
    if usage.web_limit is not None:
        lines.append(_web_line(usage))
    return lines
