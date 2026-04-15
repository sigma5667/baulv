"""Stateful session rows backing the JWTs.

Each issued access token carries a unique ``jti`` (JWT ID) claim that
matches the ``jti`` column here. That makes tokens revocable: the
request-time auth dependency looks up the session and rejects the
request if ``revoked_at`` is set.

Why bother with state for JWTs? Stateless JWTs can't be invalidated
short of rotating the signing key, which also kicks every other user
off. DSGVO-friendly password changes need "log me out everywhere
else" semantics, and users must be able to see and kill active
sessions individually — both require per-token state.

The cost is one extra DB read per authenticated request. Acceptable
at our scale; if it ever isn't, a small in-process LRU keyed on ``jti``
that holds the ``revoked_at`` flag for a few seconds would close 99%
of the gap without making revocation meaningfully slower.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import INET, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # JWT ``jti`` claim — the cross-reference between a presented token
    # and this row. Indexed+unique so lookup per request is a single
    # B-tree probe.
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Soft-revoke: the row stays around so the user can still see "this
    # session was killed at X" in their audit view. Hard-deleted only
    # via garbage collection of expired rows (not implemented yet).
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
