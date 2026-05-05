import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    company_name: Mapped[str | None] = mapped_column(String(255))
    subscription_plan: Mapped[str] = mapped_column(String(50), default="basis")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    # DSGVO-compliant opt-in for marketing email. Defaults to False so
    # accounts start with no consent — required under Art. 7 DSGVO.
    marketing_email_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    # Versions of the legal documents this user has CURRENTLY
    # accepted. Distinct from the canonical "what version is live
    # right now" pinned in ``app/legal_versions.py`` — when those
    # diverge, the SPA prompts the user via ``ConsentRefreshModal``
    # to re-accept on next login. NULL means "grandfathered in" —
    # users who registered before consent_snapshots existed (v23.2)
    # keep these columns null until a separate retroactive-consent
    # campaign runs (DS-1 follow-up). The full history of every
    # acceptance lives in the ``consent_snapshots`` table.
    current_privacy_version: Mapped[str | None] = mapped_column(String(20))
    current_terms_version: Mapped[str | None] = mapped_column(String(20))
    # v23.8 — DSGVO Art. 6(1)(a) consent for anonymised usage
    # analytics. Default False; the analytics service short-circuits
    # without writing if this flag is False, so opt-in must be a
    # clear affirmative action by the user. The corresponding
    # consent_snapshots row is written by the consent service so the
    # opt-in moment is auditable independently of this column.
    analytics_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # User-self-selected branch (architect / builder / subcontractor /
    # unknown). Captured in every analytics event so the admin
    # dashboard can segment usage by branch without ever joining
    # back to identifying user data. NULL = "user hasn't picked
    # yet" (distinct from explicit "unknown").
    industry_segment: Mapped[str | None] = mapped_column(String(30))
    # v23.8 — admin gate. Replaces the v23.3 email allowlist for the
    # per-user analytics dashboard endpoint. The allowlist still
    # serves as a fallback (settings.admin_email_list); a user
    # with EITHER ``is_admin=True`` OR an email on the allowlist
    # passes the gate.
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
