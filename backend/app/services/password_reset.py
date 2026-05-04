"""Password-reset token lifecycle helpers (DS-3 / v23.4).

Three operations, one module:

* ``mint_reset_token`` — generate a fresh URL-safe token, hash it,
  invalidate any prior outstanding tokens for the user, persist the
  new row. Returns the **plaintext** so the caller can email it out.
* ``verify_reset_token`` — look up a presented token by hash, reject
  if it's missing / used / expired. Returns the matching
  ``PasswordResetToken`` row + its user on success.
* ``mark_token_used`` — atomic flip of ``used_at`` from NULL to
  ``now()``. Single-use guarantee.

The plaintext token never reaches the database. We hash on the way
in (``mint``) and re-hash on the way out (``verify``). The same
SHA-256 pattern as ``app/api_key_auth.py`` — see that module for the
"fast hash because the entropy is in the token, not in a low-entropy
password" reasoning.

DSGVO note
----------

When a previous outstanding token exists for the same user and we
mint a new one, we *invalidate* (set ``used_at = now()``) the old
rows rather than delete them. The audit trail "the user requested
two resets in the same hour" is part of the security-incident
forensic record under Art. 32. Retention sweeps the rows out
eventually.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.password_reset_token import PasswordResetToken
from app.db.models.user import User

logger = logging.getLogger(__name__)


# 1 hour validity matches the spec ("1 Stunde gültig" hint in the
# email body). Short enough that a stolen email body is rarely still
# usable; long enough that a user who puts the email aside for a
# coffee can still come back to it.
RESET_TOKEN_TTL = timedelta(hours=1)

# 32 bytes URL-safe = 43 characters, ~256 bits of entropy. Same as
# the JWT ``jti`` minter; reuse the constant idea for consistency.
RESET_TOKEN_BYTES = 32


def _hash_token(token: str) -> str:
    """SHA-256 hex of the URL-safe token. 64 chars, lowercase."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def mint_reset_token(
    db: AsyncSession,
    *,
    user: User,
) -> str:
    """Generate, persist, and return a fresh password-reset token.

    Side effects on the DB session (caller owns the commit):

    1. Any outstanding (``used_at IS NULL``) reset rows for this user
       get marked used, so the most-recent email is the only valid one.
       We do this *before* inserting the new row so a concurrent
       ``verify`` on an old token loses the race cleanly.
    2. A new row is inserted with the SHA-256 of the plaintext.

    Returns the plaintext token. The caller embeds it in the email
    link and then discards it.
    """
    # Step 1: invalidate prior outstanding tokens. Bulk UPDATE is
    # cheaper than fetching + flagging individually, and lets the DB
    # serialise multiple concurrent ``mint`` calls for the same user
    # cleanly via row-level locking.
    now = datetime.now(timezone.utc)
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )

    # Step 2: mint and persist.
    plaintext = secrets.token_urlsafe(RESET_TOKEN_BYTES)
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(plaintext),
        expires_at=now + RESET_TOKEN_TTL,
        # ``created_at`` defaults to ``datetime.utcnow`` in the model.
    )
    db.add(row)
    await db.flush()

    # Log the row id, NOT the plaintext or hash. The id is enough to
    # correlate with the audit-log entry written by the endpoint.
    logger.info(
        "password_reset.token_minted user_id=%s token_id=%s expires_at=%s",
        user.id,
        row.id,
        row.expires_at.isoformat(),
    )
    return plaintext


async def verify_reset_token(
    db: AsyncSession,
    *,
    presented_token: str,
) -> tuple[PasswordResetToken, User] | None:
    """Resolve a presented plaintext token to its row + user.

    Returns ``None`` on any failure (no match / expired / already
    used / orphaned user). The caller translates ``None`` into a
    400-with-generic-message — we don't surface the specific failure
    reason to the client to avoid handing a probe a "this token was
    valid but expired" oracle.
    """
    if not presented_token:
        return None

    presented_hash = _hash_token(presented_token)

    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == presented_hash
        )
    )
    row = result.scalars().first()
    if row is None:
        return None
    if row.used_at is not None:
        # Already redeemed. The audit row stays for evidence.
        return None
    # ``expires_at`` is timezone-aware UTC. Compare against an aware
    # ``now`` so we don't trip the sometimes-naive-sometimes-aware
    # comparison error pytest catches in our other date code.
    now = datetime.now(timezone.utc)
    expires = row.expires_at
    if expires.tzinfo is None:
        # Defensive: SQLite (test harness) round-trips ``DateTime`` as
        # naive even when the column declares timezone=True. Treat
        # naive timestamps as UTC.
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        return None

    user = await db.get(User, row.user_id)
    if user is None:
        # FK is CASCADE; an orphaned token shouldn't exist. Soft-fail.
        return None

    return row, user


async def mark_token_used(
    db: AsyncSession,
    *,
    token_id: UUID,
) -> None:
    """Flip ``used_at`` from NULL to ``now()``. Idempotent.

    Run inside the same transaction as the password update so a
    failed bcrypt step doesn't burn the token.
    """
    now = datetime.now(timezone.utc)
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.id == token_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
