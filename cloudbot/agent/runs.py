"""In-memory, per-channel log of what the agent recently produced (videos, songs, ...).

Shared by every producer sub-agent so a follow-up like "make it longer" can find the last
artifact and iterate on it. No database — entries expire after a timeout and the newest few are
injected into a sub-agent's prompt so it knows what it already made.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

_TTL_S = 45 * 60
_MAX_PER_CHANNEL = 20
_RECENT_IN_PROMPT = 6


_DETAIL_MAX = 1500


@dataclass(frozen=True)
class RunRecord:
    kind: str
    summary: str
    url: str
    ts: float
    detail: str = ""


_RUNS: dict[str, deque[RunRecord]] = {}
# Producers record from executor threads (e.g. the Suno poll hook) while readers run on the
# event loop, and a deque mutated mid-iteration raises.
_LOCK = threading.Lock()


def _prune(log: deque[RunRecord]) -> None:
    cutoff = time.time() - _TTL_S
    while log and log[0].ts < cutoff:
        log.popleft()


def record_run(
    channel: str, kind: str, summary: str, url: str, detail: str = ""
) -> None:
    """Remember one finished artifact for this channel; ``detail`` carries what a
    follow-up needs to recreate it (e.g. the Strudel code). No-op without a
    channel or url."""
    if not (channel and url):
        return
    with _LOCK:
        log = _RUNS.setdefault(channel, deque(maxlen=_MAX_PER_CHANNEL))
        _prune(log)
        log.append(
            RunRecord(
                kind=kind,
                summary=summary.strip()[:160],
                url=url,
                ts=time.time(),
                detail=detail.strip()[:_DETAIL_MAX],
            )
        )


def recent_runs(
    channel: str, kind: str | None = None, n: int = _RECENT_IN_PROMPT
) -> list[RunRecord]:
    """The newest live artifacts for this channel, optionally filtered by kind (newest first)."""
    with _LOCK:
        log = _RUNS.get(channel)
        if log is None:
            return []
        _prune(log)
        if not log:
            del _RUNS[channel]
            return []
        items = [
            record for record in reversed(log) if kind is None or record.kind == kind
        ]
    return items[:n]


def recent_block(channel: str, kind: str) -> str:
    """A markdown list of recent artifacts of one kind, or '' when there are none."""
    runs = recent_runs(channel, kind)
    if not runs:
        return ""
    return "\n".join(f'- "{record.summary}" — {record.url}' for record in runs)
