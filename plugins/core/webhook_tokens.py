"""
Temporary webhook token management for secure webhook authentication.

Tokens are automatically cleaned up on expiration.
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Column, DateTime, String, Table, select

from cloudbot.util import database

webhook_tokens_table = Table(
    "webhook_tokens",
    database.metadata,
    Column("token", String, primary_key=True),
    Column("created_at", DateTime),
    Column("expires_at", DateTime),
)


def generate_webhook_token(db, expiration_hours: int = 24) -> str:
    """Generate a new temporary webhook token with expiration."""
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires_at = now + timedelta(hours=expiration_hours)

    db.execute(
        webhook_tokens_table.insert().values(
            token=token,
            created_at=now,
            expires_at=expires_at,
        )
    )
    db.commit()

    return token


def delete_webhook_token(db, token: str) -> bool:
    """Delete a specific webhook token."""
    result = db.execute(webhook_tokens_table.delete().where(webhook_tokens_table.c.token == token))
    db.commit()
    return result.rowcount > 0


def cleanup_expired_tokens(db) -> None:
    """Remove all expired tokens from database."""
    now = datetime.now()
    db.execute(webhook_tokens_table.delete().where(webhook_tokens_table.c.expires_at < now))
    db.commit()


def is_token_valid(db, token: str) -> bool:
    """Check if token exists and is not expired."""
    now = datetime.now()
    result = db.execute(
        select([webhook_tokens_table.c.token])
        .where(webhook_tokens_table.c.token == token)
        .where(webhook_tokens_table.c.expires_at > now)
    ).fetchone()

    return result is not None


def verify_webhook_signature(payload: dict[str, Any], signature: str, signing_key: str) -> bool:
    """Verify HMAC signature for webhook payload."""
    payload_json = json.dumps(payload, sort_keys=True)
    expected_signature = hmac.new(signing_key.encode(), payload_json.encode(), hashlib.sha256).hexdigest()
    received_digest = signature.split("=")[1] if "=" in signature else signature
    return hmac.compare_digest(expected_signature, received_digest)
