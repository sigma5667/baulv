"""Append-only audit log for sensitive account events.

Every row records one action a user (or system) took that is relevant
under DSGVO Art. 32 (security of processing) or useful for the user's
own review: logins, password changes, data exports, account deletion,
session revocations. The table is never updated or deleted from at
runtime — the only mutation is INSERT. This gives us a tamper-evident
trail and keeps the schema simple.

``user_id`` uses ``ON DELETE SET NULL`` rather than CASCADE so that
account-deletion events (and everything before them) survive the
account itself. The reference is anonymized but the event record
remains, which is what the regulator expects: "we know this user
existed and deleted their account on date X" without retaining the
link to a still-queryable identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLogEntry(Base):
    __tablename__ = "audit_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )
