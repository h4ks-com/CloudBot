"""
IRCv3 draft/multiline BATCH protocol utilities
"""

import secrets

MAX_BATCH_LINE_BYTES = 400


def supports_multiline(conn) -> bool:
    """Check if connection supports draft/multiline capability"""
    server_caps = conn.memory.get("server_caps", {})
    return server_caps.get("draft/multiline", False)


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


def send_batch_multiline(conn, target: str, lines: list[str]) -> None:
    """Send lines using BATCH draft/multiline protocol"""
    if not lines:
        return

    batch_id = generate_batch_id()
    conn.send(f"BATCH +{batch_id} draft/multiline {target}")

    for line in lines:
        chunks = split_long_line(line)

        if len(chunks) == 1:
            conn.send(f"@batch={batch_id} PRIVMSG {target} :{chunks[0]}")
        else:
            conn.send(f"@batch={batch_id} PRIVMSG {target} :{chunks[0]}")
            for chunk in chunks[1:]:
                conn.send(
                    f"@batch={batch_id};draft/multiline-concat PRIVMSG {target} :{chunk}"
                )

    conn.send(f"BATCH -{batch_id}")
