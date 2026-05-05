"""DSGVO-konforme Nutzungs-Analytics (v23.8).

Append-only event ledger that powers the future analytics features
(usage statistics, benchmarks, KI-training corpus). The whole
pipeline is gated on ``users.analytics_consent`` — no event ever
lands in this table without explicit user opt-in.

Anonymisation
-------------

The row carries an *anonymised* user identifier
(``anonymous_user_id``) computed as ``sha256(user.id || salt)``.
The salt lives in ``settings.analytics_salt`` and is mandatory in
production; without it the service refuses to record anything.

Same user → same hash (so we can correlate that two events came
from the same person), but the hash is not reversible without the
salt — and the salt never leaves the server. This is the
standard "pseudonymisation" pattern under DSGVO Art. 4 Nr. 5:
identifiers that can't be assigned to a specific data subject
without additional information held separately.

User deletion (Art. 17) does not cascade-delete these rows —
intentional. The whole point of this table is to preserve the
*aggregate* signal even as individuals come and go. Without the
salt, the rows are statistical data, not personal data.

Whitelisting
------------

The ``event_data`` JSONB column is constrained at the *service*
layer — see ``app/services/analytics.py``. Each ``event_type``
has an explicit set of allowed keys; anything else is dropped
before the row is built. The DB doesn't enforce this; the
service is the source of truth.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# ---------------------------------------------------------------------------
# Canonical event types (whitelist). Strings, not Enum, so adding a
# new event is one line here without a migration.
# ---------------------------------------------------------------------------

EVENT_PROJECT_CREATED = "project_created"
EVENT_LV_CREATED = "lv_created"
EVENT_TEMPLATE_USED = "template_used"
EVENT_POSITION_UPDATED = "position_updated"
EVENT_PLAN_ANALYZED = "plan_analyzed"
EVENT_USER_SIGNUP = "user_signup"
EVENT_FEATURE_USED = "feature_used"

# Must stay in sync with the per-event-type allow-list in
# ``app/services/analytics.py``.
ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_PROJECT_CREATED,
        EVENT_LV_CREATED,
        EVENT_TEMPLATE_USED,
        EVENT_POSITION_UPDATED,
        EVENT_PLAN_ANALYZED,
        EVENT_USER_SIGNUP,
        EVENT_FEATURE_USED,
    }
)


# ---------------------------------------------------------------------------
# Industry segments. User self-classifies at signup or in settings.
# ---------------------------------------------------------------------------

INDUSTRY_ARCHITECT = "architect"
INDUSTRY_BUILDER = "builder"
INDUSTRY_SUBCONTRACTOR = "subcontractor"
INDUSTRY_UNKNOWN = "unknown"

ALLOWED_INDUSTRY_SEGMENTS: frozenset[str] = frozenset(
    {
        INDUSTRY_ARCHITECT,
        INDUSTRY_BUILDER,
        INDUSTRY_SUBCONTRACTOR,
        INDUSTRY_UNKNOWN,
    }
)


class UsageAnalyticsEvent(Base):
    """One row per consented user-action.

    Never delete rows from this table. The DSGVO rationale is that
    the rows are pseudonymised at write time — there is no PII to
    delete. The on-disk record is statistical data, not personal
    data, the moment the salt-hashed user_id replaces the real one.

    For user-side data export (Art. 20), the
    ``GET /me/analytics-events`` endpoint computes the user's own
    hash and returns the matching rows; for user-side deletion
    requests (Art. 17), there's nothing to delete from this table
    — the explanation lives in the privacy policy v1.1.
    """

    __tablename__ = "usage_analytics"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # JSONB. Service-layer-validated against a per-event-type
    # whitelist; anything not on the list is dropped before insert.
    event_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 64-char hex SHA-256. Pseudonymised; no FK back to ``users``.
    anonymous_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Bundesland-level only (e.g. "AT-5" for Salzburg). NULL when
    # the project has no parseable address.
    region_code: Mapped[str | None] = mapped_column(String(10))
    # Self-selected branch. NULL = user hasn't picked.
    industry_segment: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
