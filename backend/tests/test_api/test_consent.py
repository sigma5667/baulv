"""Tests for the v23.2 DSGVO Art. 7 consent-snapshot mechanism.

Coverage matches the DS-1 spec:

  1. Registration without privacy/terms → 400 (Pydantic field
     validation; the strings are required schema fields).
  2. Registration with stale version strings → 409 (server-side
     mismatch — frontend was outdated).
  3. Registration success → user has versions set, snapshot row
     persisted with all required forensic fields.
  4. Marketing optin defaults to False on registration without
     the explicit checkbox.
  5. Stale ``current_privacy_version`` triggers the refresh modal
     flow; ``POST /me/consent/refresh`` updates the user and writes
     a fresh snapshot tagged ``privacy_update`` or ``terms_update``.
  6. IP from XFF header lands on the snapshot row (forensic).
  7. Marketing-flag toggle via PUT /me/privacy emits a
     ``marketing_optin_change`` snapshot.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import refresh_consent, register, update_privacy_settings
from app.db.models.consent import (
    EVENT_MARKETING_OPTIN_CHANGE,
    EVENT_PRIVACY_UPDATE,
    EVENT_REGISTRATION,
    EVENT_TERMS_UPDATE,
    ConsentSnapshot,
)
from app.db.models.user import User
from app.legal_versions import (
    PRIVACY_POLICY_VERSION,
    TERMS_VERSION,
)
from app.schemas.user import (
    ConsentRefreshRequest,
    PrivacySettingsUpdate,
    UserRegister,
)


def _mock_request(*, ip: str | None = None, user_agent: str | None = None):
    """Stand-in for the FastAPI Request used by the consent helpers.

    Mirrors ``services/audit._client_ip``'s lookup: XFF first,
    otherwise the socket peer's host. Test helpers default to no
    IP and no UA — pass kwargs to populate.
    """
    request = MagicMock()
    headers = {}
    if user_agent:
        headers["user-agent"] = user_agent
    if ip:
        headers["x-forwarded-for"] = ip
    request.headers = headers
    request.client = None
    return request


# ---------------------------------------------------------------------------
# Schema-level validation — missing version strings rejected at parse time
# ---------------------------------------------------------------------------


def test_register_payload_requires_privacy_and_terms_versions():
    """``UserRegister`` declares ``accepted_privacy_version`` and
    ``accepted_terms_version`` as plain ``str`` (no default), so a
    payload missing them fails Pydantic validation before the
    endpoint runs. This is the API-surface guarantee that a request
    can never reach our consent-snapshot logic without consent
    fields present."""
    with pytest.raises(Exception):  # ValidationError, raised by Pydantic
        UserRegister(
            email="x@example.com",
            password="strongpass123",
            full_name="Test",
            # accepted_privacy_version missing
            accepted_terms_version=TERMS_VERSION,
        )


# ---------------------------------------------------------------------------
# Registration success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_success_sets_versions_and_writes_snapshot(
    db_session: AsyncSession,
):
    """Happy path: payload with current versions creates a user
    whose ``current_*_version`` fields equal the canonical pins,
    AND a registration-event snapshot lives in
    ``consent_snapshots`` carrying both versions + the marketing
    choice + the request IP."""
    payload = UserRegister(
        email=f"new-{uuid.uuid4()}@example.com",
        password="strongpass123",
        full_name="Maria Tester",
        company_name="Tester GmbH",
        accepted_privacy_version=PRIVACY_POLICY_VERSION,
        accepted_terms_version=TERMS_VERSION,
        marketing_optin=True,
    )
    request = _mock_request(ip="203.0.113.42", user_agent="ua/1.0")

    result = await register(payload, request, db=db_session)
    await db_session.commit()

    user = (
        await db_session.execute(
            select(User).where(User.email == payload.email.lower().strip())
        )
    ).scalars().first()
    assert user is not None
    assert user.current_privacy_version == PRIVACY_POLICY_VERSION
    assert user.current_terms_version == TERMS_VERSION
    assert user.marketing_email_opt_in is True

    snapshots = (
        await db_session.execute(
            select(ConsentSnapshot).where(ConsentSnapshot.user_id == user.id)
        )
    ).scalars().all()
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.event_type == EVENT_REGISTRATION
    assert snap.privacy_version == PRIVACY_POLICY_VERSION
    assert snap.terms_version == TERMS_VERSION
    assert snap.marketing_optin is True
    assert snap.ip_address == "203.0.113.42"
    assert snap.user_agent == "ua/1.0"

    # Endpoint also returns the user payload with the four version
    # fields populated for the SPA's needs_consent_refresh predicate.
    assert result.user.accepted_privacy_version == PRIVACY_POLICY_VERSION
    assert result.user.required_privacy_version == PRIVACY_POLICY_VERSION


@pytest.mark.asyncio
async def test_register_marketing_optin_defaults_to_false(
    db_session: AsyncSession,
):
    """DSGVO Art. 7 — pre-checked boxes are not "clear affirmative
    action". The schema default is False; omitting the field from
    the payload must produce False, not whatever Pydantic's empty-
    body coerce would do."""
    payload = UserRegister(
        email=f"def-{uuid.uuid4()}@example.com",
        password="strongpass123",
        full_name="Tester",
        accepted_privacy_version=PRIVACY_POLICY_VERSION,
        accepted_terms_version=TERMS_VERSION,
        # marketing_optin omitted — must default to False
    )
    request = _mock_request()

    await register(payload, request, db=db_session)
    await db_session.commit()

    user = (
        await db_session.execute(
            select(User).where(User.email == payload.email.lower().strip())
        )
    ).scalars().first()
    assert user is not None
    assert user.marketing_email_opt_in is False

    snapshot = (
        await db_session.execute(
            select(ConsentSnapshot).where(ConsentSnapshot.user_id == user.id)
        )
    ).scalars().first()
    assert snapshot is not None
    assert snapshot.marketing_optin is False


# ---------------------------------------------------------------------------
# Stale-version guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_rejects_stale_privacy_version(
    db_session: AsyncSession,
):
    """A frontend that hasn't fetched the latest /legal/versions
    sends an outdated ``accepted_privacy_version``. The endpoint
    must 409 with a German "please reload" message rather than
    quietly registering the user under stale text."""
    payload = UserRegister(
        email=f"stale-{uuid.uuid4()}@example.com",
        password="strongpass123",
        full_name="Tester",
        accepted_privacy_version="0.99",  # not the current value
        accepted_terms_version=TERMS_VERSION,
    )

    with pytest.raises(HTTPException) as exc_info:
        await register(payload, _mock_request(), db=db_session)
    assert exc_info.value.status_code == 409
    assert "Datenschutzerklärung" in exc_info.value.detail


@pytest.mark.asyncio
async def test_register_rejects_stale_terms_version(
    db_session: AsyncSession,
):
    """Mirror of the above for terms. Both checks fire
    independently; whichever mismatches is named in the 409
    detail so the user knows which document to re-read."""
    payload = UserRegister(
        email=f"stale-t-{uuid.uuid4()}@example.com",
        password="strongpass123",
        full_name="Tester",
        accepted_privacy_version=PRIVACY_POLICY_VERSION,
        accepted_terms_version="0.99",  # not the current value
    )

    with pytest.raises(HTTPException) as exc_info:
        await register(payload, _mock_request(), db=db_session)
    assert exc_info.value.status_code == 409
    assert "AGB" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Refresh flow — modal-driven re-acceptance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_consent_after_privacy_bump_writes_privacy_update_snapshot(
    db_session: AsyncSession,
):
    """User registered under privacy v1.0; server bumps to v1.0
    (no actual bump in test, we simulate by setting the user's
    accepted version to a stale value before calling refresh).
    The refresh endpoint accepts the current versions, updates the
    user row, and writes a snapshot tagged ``privacy_update``."""
    # Seed a user with a stale privacy version. This simulates
    # "user registered before the server bumped to PRIVACY_POLICY_VERSION".
    user = User(
        id=uuid.uuid4(),
        email=f"stale-priv-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tester",
        marketing_email_opt_in=False,
        current_privacy_version="0.99",  # stale on purpose
        current_terms_version=TERMS_VERSION,
    )
    db_session.add(user)
    await db_session.commit()

    payload = ConsentRefreshRequest(
        accepted_privacy_version=PRIVACY_POLICY_VERSION,
        accepted_terms_version=TERMS_VERSION,
        marketing_optin=False,
    )

    await refresh_consent(payload, _mock_request(ip="198.51.100.7"), user=user, db=db_session)
    await db_session.commit()

    refreshed = await db_session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.current_privacy_version == PRIVACY_POLICY_VERSION

    snapshot = (
        await db_session.execute(
            select(ConsentSnapshot).where(ConsentSnapshot.user_id == user.id)
        )
    ).scalars().first()
    assert snapshot is not None
    assert snapshot.event_type == EVENT_PRIVACY_UPDATE
    assert snapshot.privacy_version == PRIVACY_POLICY_VERSION
    assert snapshot.ip_address == "198.51.100.7"


@pytest.mark.asyncio
async def test_refresh_consent_after_terms_bump_writes_terms_update_snapshot(
    db_session: AsyncSession,
):
    """Mirror: only the terms version moved → snapshot tag is
    ``terms_update``, not ``privacy_update``."""
    user = User(
        id=uuid.uuid4(),
        email=f"stale-terms-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tester",
        marketing_email_opt_in=False,
        current_privacy_version=PRIVACY_POLICY_VERSION,
        current_terms_version="0.99",  # stale on purpose
    )
    db_session.add(user)
    await db_session.commit()

    payload = ConsentRefreshRequest(
        accepted_privacy_version=PRIVACY_POLICY_VERSION,
        accepted_terms_version=TERMS_VERSION,
        marketing_optin=False,
    )

    await refresh_consent(payload, _mock_request(), user=user, db=db_session)
    await db_session.commit()

    snapshot = (
        await db_session.execute(
            select(ConsentSnapshot).where(ConsentSnapshot.user_id == user.id)
        )
    ).scalars().first()
    assert snapshot is not None
    assert snapshot.event_type == EVENT_TERMS_UPDATE


# ---------------------------------------------------------------------------
# Marketing-optin change writes a dedicated snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_privacy_settings_update_writes_marketing_snapshot_on_flip(
    db_session: AsyncSession,
):
    """Toggling the marketing checkbox via ``PUT /me/privacy``
    must produce a ``marketing_optin_change`` snapshot — that's the
    DSGVO Art. 7 evidence row that captures "Maria turned the
    newsletter ON on date X from IP Y"."""
    user = User(
        id=uuid.uuid4(),
        email=f"mkt-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tester",
        marketing_email_opt_in=False,
        current_privacy_version=PRIVACY_POLICY_VERSION,
        current_terms_version=TERMS_VERSION,
    )
    db_session.add(user)
    await db_session.commit()

    payload = PrivacySettingsUpdate(marketing_email_opt_in=True)

    await update_privacy_settings(
        payload, _mock_request(), user=user, db=db_session
    )
    await db_session.commit()

    snapshot = (
        await db_session.execute(
            select(ConsentSnapshot)
            .where(ConsentSnapshot.user_id == user.id)
            .where(
                ConsentSnapshot.event_type == EVENT_MARKETING_OPTIN_CHANGE
            )
        )
    ).scalars().first()
    assert snapshot is not None
    assert snapshot.marketing_optin is True


@pytest.mark.asyncio
async def test_privacy_settings_idempotent_does_not_write_snapshot(
    db_session: AsyncSession,
):
    """A no-op PUT (toggle to the same value the user already has)
    must NOT spam the consent table. We only record events when the
    value actually changes."""
    user = User(
        id=uuid.uuid4(),
        email=f"noop-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tester",
        marketing_email_opt_in=True,  # already opted in
        current_privacy_version=PRIVACY_POLICY_VERSION,
        current_terms_version=TERMS_VERSION,
    )
    db_session.add(user)
    await db_session.commit()

    payload = PrivacySettingsUpdate(marketing_email_opt_in=True)

    await update_privacy_settings(
        payload, _mock_request(), user=user, db=db_session
    )
    await db_session.commit()

    snapshots = (
        await db_session.execute(
            select(ConsentSnapshot)
            .where(ConsentSnapshot.user_id == user.id)
            .where(
                ConsentSnapshot.event_type == EVENT_MARKETING_OPTIN_CHANGE
            )
        )
    ).scalars().all()
    assert snapshots == []
