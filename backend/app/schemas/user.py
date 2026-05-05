from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserRegister(BaseModel):
    """Sign-up payload. v23.2 added the three consent fields below
    so we can satisfy DSGVO Art. 7's "demonstrate consent"
    requirement at registration time. The two version strings are
    sent by the frontend (the SPA fetches them from
    ``/api/legal/versions`` or reads them from ``/auth/me``) so we
    can verify the client saw the same text we currently serve —
    a stale tab can't sneak a user in under a previous policy.

    v23.8 added the analytics opt-in + industry-segment fields
    (DSGVO Art. 6(1)(a)). Both default to "off / not selected" so
    a user who simply ticks the two mandatory boxes lands in the
    no-analytics, no-industry baseline — the sensible privacy-
    preserving default.
    """

    email: str
    password: str
    full_name: str
    company_name: str | None = None
    # Mandatory consent fields. The frontend supplies the version
    # strings it displayed; the backend rejects with 409 if they
    # don't match the canonical ``app/legal_versions.py`` values.
    accepted_privacy_version: str
    accepted_terms_version: str
    # Marketing opt-in stays optional, default false per Art. 7
    # ("clear affirmative action"). The user has to actively tick
    # the third checkbox in the SPA.
    marketing_optin: bool = False
    # v23.8 — anonymised-analytics opt-in. Default False ⇒ the
    # ``record_event`` service short-circuits without writing for
    # this user. The accompanying ``industry_segment`` field is
    # only meaningful when this is True; the frontend should hide
    # the dropdown when the checkbox is off.
    analytics_consent: bool = False
    # User-self-selected branch (architect / builder /
    # subcontractor / unknown). NULL when the user didn't pick
    # one — distinct from explicit "unknown".
    industry_segment: str | None = None


class UserLogin(BaseModel):
    email: str
    password: str


class UserUpdate(BaseModel):
    full_name: str | None = None
    company_name: str | None = None


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    company_name: str | None
    subscription_plan: str
    stripe_customer_id: str | None
    marketing_email_opt_in: bool
    # v23.2 — DSGVO Art. 7 evidence trail. ``accepted_*`` is what the
    # user has signed off on (NULL for grandfathered pre-v23.2
    # accounts); ``required_*`` is what the server currently serves.
    # The SPA computes ``needs_consent_refresh`` from the four and
    # surfaces the modal when accepted is non-null and != required.
    accepted_privacy_version: str | None = None
    accepted_terms_version: str | None = None
    required_privacy_version: str
    required_terms_version: str
    # v23.8 — analytics state. ``analytics_consent`` drives the
    # service-layer gate; ``industry_segment`` is the user's self-
    # classification. ``is_admin`` lets the frontend conditionally
    # render the admin-analytics nav entry without a separate
    # round-trip.
    analytics_consent: bool = False
    industry_segment: str | None = None
    is_admin: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class PasswordChangeRequest(BaseModel):
    """Change the current user's password.

    Requires the current password as a re-auth step — we do not trust
    the bearer token alone for sensitive account changes, since a
    stolen token shouldn't be enough to lock the real owner out.
    """

    current_password: str
    new_password: str


class AccountDeletionRequest(BaseModel):
    """Confirmation payload for DSGVO Art. 17 account deletion.

    Two barriers to accidental / malicious deletion:

    * ``password`` — proves the caller still knows the secret, not just
      that they have a valid bearer token. Protects against stolen tokens
      and shared devices.
    * ``confirmation`` — the user must type the literal string
      ``LÖSCHEN`` (checked server-side). Protects against UI bugs that
      fire a DELETE request without an explicit user action.
    """

    password: str
    confirmation: str


# ---------------------------------------------------------------------------
# Privacy settings (marketing consent etc.)
# ---------------------------------------------------------------------------

class PrivacySettingsUpdate(BaseModel):
    """Partial update for privacy-related user flags.

    All fields optional — only fields present in the request body are
    written back. This lets the frontend toggle one switch at a time
    without having to re-submit everything.
    """

    marketing_email_opt_in: bool | None = None


class ConsentRefreshRequest(BaseModel):
    """Payload for ``POST /api/auth/me/consent/refresh``.

    Fired by the SPA's ``ConsentRefreshModal`` when a user re-
    accepts updated legal documents. Both version strings must
    match what the server currently serves; the marketing flag
    can change as part of the same refresh (the modal exposes
    the same checkbox the registration form does).

    v23.8 — analytics fields exposed too. Existing users hitting
    the modal because of the privacy-policy v1.1 bump can opt in
    (or stay opted out) to the new analytics pipeline as part of
    the same gesture.
    """

    accepted_privacy_version: str
    accepted_terms_version: str
    marketing_optin: bool
    analytics_consent: bool = False
    industry_segment: str | None = None


class LegalVersionsResponse(BaseModel):
    """Public ``GET /api/legal/versions`` payload — what the
    frontend renders next to the consent checkboxes
    ("Datenschutzerklärung Version 1.0 vom 27.04.2026")."""

    privacy_version: str
    privacy_date: str
    terms_version: str
    terms_date: str


# ---------------------------------------------------------------------------
# Session management (Art. 32 — traceable sessions)
# ---------------------------------------------------------------------------

class SessionResponse(BaseModel):
    id: UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    # Server-assigned flag indicating which row corresponds to the token
    # used to make the /sessions request. The frontend uses this to
    # label one row as "this device" and to hide its revoke button.
    is_current: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLogEntryResponse(BaseModel):
    id: UUID
    event_type: str
    meta: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# v23.8 — Analytics consent + per-user data export
# ---------------------------------------------------------------------------


class AnalyticsConsentUpdate(BaseModel):
    """Payload for ``PUT /api/auth/me/analytics-consent``.

    Both fields optional so a user can flip just the consent
    flag without re-asserting their industry, or vice versa. The
    backend rejects ``industry_segment`` values that aren't on the
    canonical set defined in ``app.db.models.analytics``.
    """

    analytics_consent: bool | None = None
    industry_segment: str | None = None


class AnalyticsConsentResponse(BaseModel):
    """Current analytics state of the authenticated user."""

    analytics_consent: bool
    industry_segment: str | None


class UserAnalyticsEventResponse(BaseModel):
    """A single row from ``usage_analytics`` shown in its
    pseudonymised form (DSGVO Art. 20 data export).

    ``anonymous_user_id`` is included so the user can verify that
    their hash matches what the operator sees — independent
    confirmation that the rows attributed to them really are
    theirs and not someone else's.
    """

    id: UUID
    event_type: str
    event_data: dict[str, Any] | None
    anonymous_user_id: str
    region_code: str | None
    industry_segment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# v23.8 — Admin analytics dashboard payload
# ---------------------------------------------------------------------------


class AdminTopTemplate(BaseModel):
    """One row in the "Top 5 verwendete Vorlagen" ranking."""

    template_id: str
    use_count: int


class AdminAnalyticsDashboard(BaseModel):
    """Aggregated metrics for the admin dashboard.

    Only aggregated values — no per-row data leaks to the
    response. Aligns with the DSGVO promise that the analytics
    pipeline never surfaces individual records.
    """

    active_users_30d: int
    projects_total: int
    projects_last_30d: int
    avg_positions_per_lv: float
    industry_distribution: dict[str, int]
    top_templates: list[AdminTopTemplate]
    generated_at: datetime
