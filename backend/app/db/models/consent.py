"""Append-only DSGVO Art. 7 evidence ledger.

Every consent action ever performed by a user — registration,
re-acceptance after a privacy-policy bump, marketing-opt-in toggle —
writes one row here. The point is to be able to answer "did this
user agree to *this exact text* of the privacy policy on date X?"
years later, even if the user has since deleted their account.

The user_id FK is ``ON DELETE SET NULL``. DSGVO Art. 17 (right to
erasure) requires we wipe the user when asked, but Art. 7 Abs. 1
("the controller shall be able to demonstrate that the data
subject has consented") requires we keep the *fact* that consent
happened, with date and text version. Setting NULL on the FK is
the standard "anonymise but preserve" pattern for this conflict.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# Accepted ``event_type`` values. New triggers should follow the same
# noun.verb / single-word convention so the audit-log viewer can
# group them sanely.
EVENT_REGISTRATION = "registration"
EVENT_PRIVACY_UPDATE = "privacy_update"
EVENT_TERMS_UPDATE = "terms_update"
EVENT_MARKETING_OPTIN_CHANGE = "marketing_optin_change"
# v23.8 — DSGVO Art. 7 evidence trail for the optional analytics
# opt-in. Fired both on initial sign-up (when the box was ticked)
# and every time the user toggles the flag in privacy settings.
# Stored alongside the legal-version pins so we can reconstruct
# the exact privacy-policy text the user saw when they consented.
EVENT_ANALYTICS_OPTIN_CHANGE = "analytics_optin_change"


class ConsentSnapshot(Base):
    __tablename__ = "consent_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # ``ON DELETE SET NULL`` per the module docstring — the snapshot
    # must outlive the user row to satisfy DSGVO Art. 7's evidence
    # requirement, but loses its identifying link on user deletion.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    # Trigger that produced this snapshot. See module-level constants
    # for the canonical set of values.
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    # Versions the user accepted at this moment. Both can be NULL on
    # synthetic events (e.g. a marketing-opt-in flip that doesn't
    # affect the legal-text consent state); registration always
    # populates both.
    privacy_version: Mapped[str | None] = mapped_column(String(20))
    terms_version: Mapped[str | None] = mapped_column(String(20))
    # The marketing-email opt-in choice at the moment of the event.
    # We always record it — even on a privacy_update event — so the
    # trail is complete and the user's consent state can be
    # reconstructed entirely from this table.
    marketing_optin: Mapped[bool] = mapped_column(Boolean, default=False)
    # v23.8 — analytics opt-in choice at the moment of the event.
    # Default False matches the schema default on ``users``; we
    # record it on every snapshot (not just analytics-toggle
    # snapshots) so a single row carries the user's full consent
    # state — same pattern as ``marketing_optin``. NULL stays a
    # valid state for old rows that were written before v23.8;
    # the column is set up with a server default so the migration
    # backfill is automatic.
    analytics_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Forensic context. Helpful when a user disputes that *they*
    # agreed (vs someone using their device); also part of "the
    # circumstances in which the data was processed" expected under
    # Art. 30 DSGVO.
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )
