"""Programmatic-access tokens for headless agents (MCP / n8n / cron).

Why this exists separately from ``user_sessions``
=================================================

JWT-based ``UserSession`` rows are tuned for **interactive** clients:
they pin to a 7-day TTL, they record User-Agent + IP, they show up in
the Settings → Sessions list as "this device". None of that fits a
machine principal:

* A 7-day TTL means an MCP server in Claude Desktop or an n8n workflow
  silently breaks every Monday morning.
* Per-request browser metadata (UA, IP) is misleading for automation —
  the n8n cloud worker has no meaningful UA.
* Mixing agent sessions into the device list pollutes the UX
  ("strange device on …" alarms) and the wrong Revoke button kills the
  user's actual phone session.

So agents get a **separate credential type**: long-lived, scoped to one
user, presented as ``Authorization: Bearer pat_...``, looked up in a
dedicated table that the device-sessions UI never touches.

Storage model
=============

The full token never lives in the database. We persist:

* ``key_prefix`` — the first ~12 visible characters (``pat_xxxxxxxx``).
  Indexable, listable, surfaced to the user as a recognisable handle
  ("which key did I see in the Stripe webhook again?"). Ambiguous on
  its own — collisions are theoretically possible across users.
* ``key_hash`` — SHA-256 hex of the **full** token. Verification on
  presentation re-hashes and compares. SHA-256 (not bcrypt) is the
  right tool here: the entropy is in the token (32 random bytes /
  256 bits), not in a low-entropy password, so we don't need bcrypt's
  slow-path defence — we *do* need fast verification on every
  authenticated request.

The plaintext token is shown to the user **once**, at creation time,
and then discarded. Revocation is soft (``revoked_at`` set) so the
audit row survives.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # Human-friendly label the user picks at creation time
    # ("Claude Desktop", "n8n production", "local script"). Free text,
    # only constrained by length.
    name: Mapped[str] = mapped_column(String(100))
    # First ~12 chars including the ``pat_`` scheme prefix. Indexed so
    # presentation-side lookups are O(1) before we hash-compare.
    key_prefix: Mapped[str] = mapped_column(String(20), index=True)
    # SHA-256 hex of the full token (64 chars). Fixed-length so a
    # length-based mismatch is impossible to confuse with a bcrypt
    # boundary error.
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    # Touched on every successful auth (best-effort, fire-and-forget on
    # the request path). NULL means never used since creation.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional self-destruct timestamp. NULL = never expires (current
    # default). Once ``now() >= expires_at``, ``verify_pat`` rejects
    # the credential the same way ``revoked_at`` does — but without a
    # human pressing a button. The two states coexist (a key can be
    # both expired *and* later revoked) so the audit history stays
    # coherent.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Soft delete. Revoked keys still match by ``key_prefix`` for
    # audit-trail readability; the verify step rejects them.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
