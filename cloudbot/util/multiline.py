"""
IRCv3 draft/multiline BATCH protocol utilities
"""

import secrets

MAX_BATCH_LINE_BYTES = 400


def supports_multiline(conn) -> bool:
    """Check if connection supports draft/multiline capability"""
    server_caps = conn.memory.get("server_caps", {})
    return bool(server_caps.get("draft/multiline", False))


def multiline_limits(conn) -> tuple[int | None, int | None]:
    """Negotiated (max_bytes, max_lines) from the draft/multiline cap value,
    each None when the server didn't advertise it."""
    for cap in conn.memory.get("available_caps") or []:
        if cap.name.casefold() != "draft/multiline":
            continue
        params: dict[str, str] = {}
        for kv in (cap.value or "").split(","):
            key, sep, val = kv.partition("=")
            if sep:
                params[key] = val
        max_bytes = int(params["max-bytes"]) if "max-bytes" in params else None
        max_lines = int(params["max-lines"]) if "max-lines" in params else None
        return max_bytes, max_lines
    return None, None


def generate_batch_id() -> str:
    """Generate unique batch ID"""
    return secrets.token_hex(8)


def split_long_line(
    text: str, max_bytes: int = MAX_BATCH_LINE_BYTES
) -> list[str]:
    """Split line into chunks if it exceeds byte limit"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return [text]

    chunks = []
    current_chunk = b""

    for char in text:
        char_bytes = char.encode("utf-8")
        if len(current_chunk) + len(char_bytes) > max_bytes:
            chunks.append(current_chunk.decode("utf-8"))
            current_chunk = char_bytes
        else:
            current_chunk += char_bytes

    if current_chunk:
        chunks.append(current_chunk.decode("utf-8"))

    return chunks


def _format_tags_str(tags: dict | None, extra: str = "") -> str:
    if not tags and not extra:
        return ""
    parts: list[str] = []
    if tags:
        parts.extend(f"{k}={v}" if v else k for k, v in tags.items())
    if extra:
        parts.append(extra)
    return "@" + ";".join(parts) + " "


def _wire_chunks(
    lines: list[str], per_line_bytes: int
) -> list[tuple[str, bool]]:
    """Expand lines into (chunk, is_concat) fragments; is_concat marks a
    continuation of an over-length line that must rejoin without a separator."""
    chunks: list[tuple[str, bool]] = []
    for line in lines:
        for i, chunk in enumerate(split_long_line(line, per_line_bytes)):
            chunks.append((chunk, i > 0))
    return chunks


def _emit_batch(
    conn, target: str, chunks: list[tuple[str, bool]], tags: dict | None
) -> None:
    batch_id = generate_batch_id()
    conn.send(
        f"{_format_tags_str(tags)}BATCH +{batch_id} draft/multiline {target}"
    )
    for chunk, is_concat in chunks:
        extra = f"batch={batch_id}"
        if is_concat:
            extra += ";draft/multiline-concat"
        conn.send(f"{_format_tags_str(None, extra)}PRIVMSG {target} :{chunk}")
    conn.send(f"BATCH -{batch_id}")


def send_batch_multiline(
    conn, target: str, lines: list[str], tags: dict | None = None
) -> None:
    """Send lines using BATCH draft/multiline, splitting into several batches
    when the negotiated max-bytes / max-lines would be exceeded."""
    if not lines:
        return

    max_bytes, max_lines = multiline_limits(conn)
    per_line = MAX_BATCH_LINE_BYTES
    if max_bytes is not None:
        per_line = min(per_line, max_bytes)

    batch: list[tuple[str, bool]] = []
    batch_bytes = 0
    first = True

    for chunk, is_concat in _wire_chunks(lines, per_line):
        chunk_bytes = len(chunk.encode("utf-8")) + 1
        over_lines = max_lines is not None and len(batch) >= max_lines
        over_bytes = (
            max_bytes is not None and batch_bytes + chunk_bytes > max_bytes
        )
        if batch and (over_lines or over_bytes) and not is_concat:
            _emit_batch(conn, target, batch, tags if first else None)
            batch, batch_bytes, first = [], 0, False
        batch.append((chunk, is_concat))
        batch_bytes += chunk_bytes

    if batch:
        _emit_batch(conn, target, batch, tags if first else None)
