"""
IRCv3 draft/multiline BATCH protocol utilities
"""

import secrets

MAX_BATCH_LINE_BYTES = 400


def supports_multiline(conn) -> bool:
    """Check if connection supports draft/multiline capability"""
    server_caps = conn.memory.get("server_caps", {})
    return bool(server_caps.get("draft/multiline", False))


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


def send_batch_multiline(
    conn, target: str, lines: list[str], tags: dict | None = None
) -> None:
    """Send lines using BATCH draft/multiline protocol"""
    if not lines:
        return

    batch_id = generate_batch_id()
    batch_tags = _format_tags_str(tags)
    conn.send(f"{batch_tags}BATCH +{batch_id} draft/multiline {target}")

    for line in lines:
        chunks = split_long_line(line)
        batch_tag = f"batch={batch_id}"

        for i, chunk in enumerate(chunks):
            extra = batch_tag
            if i > 0:
                extra += ";draft/multiline-concat"
            tag_str = _format_tags_str(None, extra)
            conn.send(f"{tag_str}PRIVMSG {target} :{chunk}")

    conn.send(f"BATCH -{batch_id}")
