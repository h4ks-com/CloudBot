"""Kaggle kernels HTTP client — quota, push, poll, outputs.

Hand-rolled against Kaggle's REST API instead of the ``kaggle`` pip package:
that package calls ``api.authenticate()`` at import time, so a missing token
kills the whole bot on import, and it resolves credentials from ``~/.kaggle``,
which the container has no reason to carry.

The execution model has two consequences worth knowing before changing anything
here:

- A push IS the run. Kaggle create-or-updates the kernel and immediately runs it
  ("save & run all"); re-running means pushing again, which mints a new version.
- There is no usable cancel. ``CancelKernelSession`` needs a session id that no
  endpoint returns, so ``session_timeout_s`` sent at push time is the only
  ceiling on a runaway notebook. Always send one.
- Nothing survives between runs, and there is no cell-level execution or session
  to attach to — every push runs the whole notebook in a clean container. Setup
  (clone, pip, weight downloads) therefore repeats on every run unless it is done
  once in its own kernel and mounted into later ones via ``kernel_sources``.
"""

import hashlib
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal, NamedTuple

import nbformat
import requests

from cloudbot.bot import CloudBot

API = "https://www.kaggle.com/api/v1"
_HTTP_TIMEOUT = 60
_ARTIFACT_MAX_BYTES = 25 * 1024 * 1024

_SLUG_MAX = 50
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# ipykernel greets every code cell with this on stderr, so it scales with cell
# count and would crowd the real output out of the log tail. Matched on its exact
# wording rather than the bare "N.NNs - " prefix, which a notebook's own timing
# print would collide with.
_DEBUGGER_NOISE_RE = re.compile(
    r"\d+\.\d+s - (Debugger warning|Note: Debugging will proceed|"
    r"make the debugger miss|to python to disable frozen modules)"
)


class KernelState(str, Enum):
    """Kaggle's lifecycle states, normalised to bare lowercase (see status())."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"
    CANCEL_ACKNOWLEDGED = "cancelacknowledged"


# A run killed by session_timeout_s parks in CANCEL_ACKNOWLEDGED permanently; it
# never reaches COMPLETE or ERROR. A poll loop watching only those two spins
# until its own deadline.
TERMINAL_STATES = frozenset(
    {
        KernelState.COMPLETE,
        KernelState.ERROR,
        KernelState.CANCEL_ACKNOWLEDGED,
    }
)


class KaggleError(Exception):
    """Kaggle API call failed."""


class KaggleNotConfigured(KaggleError):
    """No Kaggle API token in config."""


class ArtifactTooLarge(KaggleError):
    """Output file is too big to mirror to the paste service.

    Distinct from a transport failure: the notebook and the file are both fine,
    only the share link is unavailable.
    """


def token_from_bot(bot: CloudBot) -> str:
    token = bot.config.get_api_key("kaggle") or ""
    if not token:
        raise KaggleNotConfigured("kaggle API token not configured")
    return token


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# Kaggle's JSON is the one genuinely dynamic boundary here: the shape differs per
# endpoint and is the server's to change. It is narrowed with the _as_* helpers
# and parsed into a typed record at the edge of every public function, so nothing
# untyped escapes this module.
JsonObject = dict[str, object]


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: object) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) else 0
    )


def _as_object(value: object) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _as_array(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _json_or_raise(resp: requests.Response, what: str) -> JsonObject:
    if "json" not in resp.headers.get("content-type", ""):
        raise KaggleError(
            f"{what}: HTTP {resp.status_code} (non-JSON response)"
        )
    parsed = resp.json()
    if not isinstance(parsed, dict):
        raise KaggleError(f"{what}: expected a JSON object")
    data: JsonObject = parsed
    if resp.status_code >= 400:
        msg = (
            _as_str(data.get("message"))
            or _as_str(data.get("error"))
            or str(resp.status_code)
        )
        raise KaggleError(f"{what}: {msg}")
    error = _as_str(data.get("error"))
    if error:
        raise KaggleError(f"{what}: {error}")
    return data


def _post(token: str, path: str, body: JsonObject, what: str) -> JsonObject:
    try:
        resp = requests.post(
            f"{API}{path}",
            headers=_headers(token),
            json=body,
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        raise KaggleError(f"{what}: {e}") from e
    return _json_or_raise(resp, what)


def _get(
    token: str, path: str, params: dict[str, str | int], what: str
) -> JsonObject:
    try:
        resp = requests.get(
            f"{API}{path}",
            headers=_headers(token),
            params=params,
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        raise KaggleError(f"{what}: {e}") from e
    return _json_or_raise(resp, what)


_USERNAME_CACHE: dict[str, str] = {}


def username(token: str) -> str:
    """Kernel ids are ``<owner>/<slug>``, and the owner is derived from the token
    itself so config only ever carries the token."""
    cached = _USERNAME_CACHE.get(token)
    if cached:
        return cached
    data = _post(
        token, "/oauth2/introspect", {"token": token}, "token introspect"
    )
    if data.get("active") is not True:
        raise KaggleError("kaggle token is inactive or revoked")
    name = _as_str(data.get("username"))
    if not name:
        raise KaggleError("kaggle token introspect returned no username")
    _USERNAME_CACHE[token] = name
    return name


def slugify(title: str) -> str:
    """Kaggle derives a kernel's slug from its title the same way; mirroring it
    locally keeps our stored ref equal to the one Kaggle actually creates.

    Pushing is create-or-update, so two titles collapsing to one slug would make
    one notebook silently overwrite another. Titles that lose their identity to
    ASCII-stripping or truncation get a digest of the full title appended.
    """
    slug = _SLUG_STRIP.sub("-", title.lower()).strip("-")
    if not slug or len(slug) > _SLUG_MAX:
        digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:8]
        head = slug[: _SLUG_MAX - 9].strip("-")
        return f"{head}-{digest}" if head else f"notebook-{digest}"
    return slug


def _duration_h(value: object) -> float:
    """Durations come back as protobuf objects on the REST host ({'seconds': N})
    and as strings ('108000s') on the RPC host."""
    if isinstance(value, dict):
        return _as_int(value.get("seconds")) / 3600
    if isinstance(value, str) and value.endswith("s"):
        try:
            return float(value[:-1] or 0) / 3600
        except ValueError:
            return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 3600
    return 0.0


@dataclass(frozen=True)
class Quota:
    used_h: float
    reserved_h: float
    total_h: float

    @property
    def remaining_h(self) -> float:
        """Hours actually still available.

        timeReserved is held by in-flight sessions; ignoring it lets two
        concurrent pushes both believe there is room.
        """
        return max(0.0, self.total_h - self.used_h - self.reserved_h)


@dataclass(frozen=True)
class QuotaReport:
    gpu: Quota
    tpu: Quota
    refresh_at: str


def _quota(block: JsonObject) -> Quota:
    return Quota(
        used_h=_duration_h(block.get("timeUsed")),
        reserved_h=_duration_h(block.get("timeReserved")),
        total_h=_duration_h(block.get("totalTimeAllowed")),
    )


def quota(token: str) -> QuotaReport:
    """Weekly accelerator quota. CPU kernels are not metered here — only GPU and
    TPU consume a weekly budget."""
    data = _get(token, "/kernels/quota", {}, "quota")
    return QuotaReport(
        gpu=_quota(_as_object(data.get("gpuQuota"))),
        tpu=_quota(_as_object(data.get("tpuQuota"))),
        refresh_at=_as_str(data.get("quotaRefreshTime")) or "?",
    )


CellKind = Literal["markdown", "code"]


class Cell(NamedTuple):
    kind: CellKind
    source: str


def build_notebook(cells: Sequence[Cell]) -> str:
    """Render cells as .ipynb JSON via nbformat.

    nbformat owns this rather than a hand-rolled dict because its `source`
    accepts either a string or a list whose every line must carry its own
    trailing newline — a plain list of lines silently fuses into one broken
    statement, which is a bug the notebook only reveals once it has run.
    """
    notebook = nbformat.v4.new_notebook()
    # new_notebook() leaves metadata empty, and Kaggle runs the notebook through
    # papermill, which refuses one that names no kernel ("No kernel name found in
    # notebook and no override provided") before executing a single cell.
    notebook.metadata.kernelspec = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    notebook.cells = [
        (
            nbformat.v4.new_code_cell(cell.source)
            if cell.kind == "code"
            else nbformat.v4.new_markdown_cell(cell.source)
        )
        for cell in cells
    ]
    rendered: str = nbformat.writes(notebook)
    return rendered


class PushResult(NamedTuple):
    ref: str
    url: str
    version: int
    # Kaggle answers an unknown kernel_sources ref with HTTP 200 and a real
    # version, then runs the kernel without the mount — so the caller has to be
    # told, or the notebook just redoes the setup it expected to find mounted.
    invalid_sources: tuple[str, ...] = ()


class OutputFile(NamedTuple):
    name: str
    url: str


def push(
    token: str,
    *,
    slug: str,
    title: str,
    cells: Sequence[Cell],
    session_timeout_s: int,
    language: str = "python",
    is_private: bool = True,
    enable_gpu: bool = False,
    enable_internet: bool = False,
    machine_shape: str | None = None,
    kernel_sources: list[str] | None = None,
) -> PushResult:
    """Create-or-update the kernel and run it.

    A failure names the cell it happened in ("Exception encountered at In [2]").

    Each ref in ``kernel_sources`` mounts that kernel's whole output read-only at
    ``/kaggle/input/notebooks/<owner>/<slug>/`` (verified against the live API —
    it is not ``/kaggle/input/<slug>/``). This is the only way to carry work
    between runs: a push always re-runs the whole notebook in a clean container,
    and Kaggle exposes no way to run one cell.
    """
    owner = username(token)
    body: JsonObject = {
        "slug": f"{owner}/{slug}",
        "newTitle": title,
        "text": build_notebook(cells),
        "language": language,
        "kernelType": "notebook",
        "isPrivate": is_private,
        "enableGpu": enable_gpu,
        "enableTpu": False,
        "enableInternet": enable_internet,
        "datasetDataSources": [],
        "kernelDataSources": kernel_sources or [],
        "competitionDataSources": [],
        "modelDataSources": [],
        "categoryIds": [],
        "sessionTimeoutSeconds": session_timeout_s,
    }
    if machine_shape:
        body["machineShape"] = machine_shape
    data = _post(token, "/kernels/push", body, "kernel push")
    return PushResult(
        ref=f"{owner}/{slug}",
        url=_as_str(data.get("url"))
        or f"https://www.kaggle.com/code/{owner}/{slug}",
        version=_as_int(data.get("versionNumber")),
        invalid_sources=tuple(
            _as_str(s)
            for s in _as_array(data.get("invalidKernelSources"))
            if _as_str(s)
        ),
    )


def status(token: str, ref: str) -> str:
    """Normalised lifecycle state. Compare against TERMINAL_STATES."""
    owner, _, slug = ref.partition("/")
    data = _get(
        token,
        "/kernels/status",
        {"userName": owner, "kernelSlug": slug},
        "kernel status",
    )
    # The wire form varies: COMPLETE / complete / KernelWorkerStatus.COMPLETE.
    raw = _as_str(data.get("status"))
    return raw.rsplit(".", 1)[-1].replace("_", "").lower()


def failure_message(token: str, ref: str) -> str:
    owner, _, slug = ref.partition("/")
    data = _get(
        token,
        "/kernels/status",
        {"userName": owner, "kernelSlug": slug},
        "kernel status",
    )
    return _as_str(data.get("failureMessage"))


def _is_platform_noise(text: str) -> bool:
    """Kaggle exports every run to HTML after the code finishes, and that step
    emits its own warnings on stderr. Dropping only those keeps the log tail for
    the notebook's real output — a traceback from the notebook itself never
    matches, since these come from Kaggle's own site-packages.
    """
    stripped = text.lstrip()
    return (
        stripped.startswith("[NbConvertApp]")
        or bool(_DEBUGGER_NOISE_RE.match(stripped))
        or ("/dist-packages/" in stripped and "SyntaxWarning" in stripped)
    )


def _render_log(raw: str) -> str:
    """Kaggle stores the run log as a JSON array of {stream_name, time, data}."""
    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(records, list):
        return raw
    return _render_records(records)


def _render_records(records: list[object]) -> str:
    lines: list[str] = []
    skip_next_continuation = False
    for entry in records:
        rec = _as_object(entry)
        text = _as_str(rec.get("data")).rstrip("\n")
        if not text:
            continue
        if _is_platform_noise(text):
            # Python prints the offending source line as a follow-up record.
            skip_next_continuation = "SyntaxWarning" in text
            continue
        if skip_next_continuation:
            skip_next_continuation = False
            if text.startswith(" ") or text.lstrip().startswith(
                ("cells[", "text =")
            ):
                continue
        prefix = "! " if rec.get("stream_name") == "stderr" else ""
        lines.append(f"{prefix}{text}")
    return "\n".join(lines)


def stream_log(token: str, ref: str, seconds: float) -> str:
    """Whatever a kernel has printed so far, including while it is still running.

    ``output()`` serves an empty log until the run reaches a terminal state, so
    without this a caller watching a live run learns nothing until it is over and
    cannot tell a slow download from a wedged one. The endpoint replays the log
    from the start, proxying an SSE feed while the session is alive and returning
    the stored blob once it is not, so the same call covers both.

    Best-effort: a run this cannot read is still a run, so failures come back as
    an empty string rather than an exception.
    """
    owner, _, slug = ref.partition("/")
    deadline = time.monotonic() + seconds
    records: list[object] = []
    try:
        with requests.get(
            f"{API}/kernels/logs/stream/{owner}/{slug}",
            headers=_headers(token),
            stream=True,
            # Read timeout, not total: it bounds the gap BETWEEN lines, which is
            # what stops a silent kernel from holding this open to the deadline.
            timeout=(10, min(max(seconds, 1.0), _HTTP_TIMEOUT)),
        ) as resp:
            if resp.status_code != 200:
                return ""
            if "event-stream" not in resp.headers.get("content-type", ""):
                return _render_log(resp.text)
            for line in resp.iter_lines(decode_unicode=True):
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload or "END_OF_LOG" in payload:
                    break
                try:
                    records.append(json.loads(payload))
                except json.JSONDecodeError:
                    continue
                if time.monotonic() >= deadline:
                    break
    except requests.RequestException:
        return _render_records(records)
    return _render_records(records)


def output(token: str, ref: str) -> tuple[list[OutputFile], str]:
    """Artifacts survive a session_timeout_s kill, so this is worth calling even
    when the run ended in cancelAcknowledged."""
    owner, _, slug = ref.partition("/")
    data = _get(
        token,
        "/kernels/output",
        {"userName": owner, "kernelSlug": slug, "pageSize": 200},
        "kernel output",
    )
    files = []
    for entry in _as_array(data.get("files")):
        item = _as_object(entry)
        url = _as_str(item.get("url"))
        if url:
            files.append(
                OutputFile(name=_as_str(item.get("fileName")) or "?", url=url)
            )
    return files, _render_log(_as_str(data.get("log")))


def fetch_file(url: str, max_bytes: int = _ARTIFACT_MAX_BYTES) -> bytes:
    """Output URLs are pre-signed; sending our auth header to them is rejected.

    Kaggle allows up to 20GB of artifacts and a GPU run's checkpoint can be
    gigabytes, so this streams and refuses anything over max_bytes rather than
    pulling it all into the bot's memory.
    """
    chunks: list[bytes] = []
    total = 0
    try:
        with requests.get(url, timeout=_HTTP_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    raise ArtifactTooLarge(
                        f"over the {max_bytes // (1024 * 1024)}MB share limit"
                    )
                chunks.append(chunk)
    except requests.RequestException as e:
        raise KaggleError(f"downloading output: {e}") from e
    return b"".join(chunks)


def delete(token: str, ref: str) -> None:
    owner, _, slug = ref.partition("/")
    _get(token, f"/kernels/delete/{owner}/{slug}", {}, "kernel delete")
