"""Tests for the v23.8 DSGVO-konforme analytics pipeline.

Coverage matches the 5-case spec:

  1. User without consent → event silently discarded.
  2. User with consent → event written, anonymised + sanitised.
  3. PII keys in event_data → entire event rejected.
  4. Consent toggle on/off changes downstream record_event behaviour.
  5. ``hash_user_id`` is irreversible (different salts produce
     different hashes; no salt → no recovery path).

Tests run against the in-memory SQLite harness from conftest. The
analytics service is dialect-portable — JSONB → TEXT in SQLite via
the conftest shim — so no extra fixtures needed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.analytics import (
    EVENT_PROJECT_CREATED,
    EVENT_USER_SIGNUP,
    UsageAnalyticsEvent,
)
from app.db.models.user import User
from app.services import analytics as analytics_service


async def _seed_user(db: AsyncSession, *, consent: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
        analytics_consent=consent,
        industry_segment="builder" if consent else None,
    )
    db.add(user)
    await db.commit()
    return user


# ---------------------------------------------------------------------------
# 1. Without consent → silent no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_discarded_when_consent_false(db_session: AsyncSession):
    """User has analytics_consent=False → no row written.

    Returns ``None`` from ``record_event`` so the caller can decide
    what to log; the DB stays clean. Crucially: the function does
    not raise even when banned PII keys are present, because the
    consent gate short-circuits before the sanitiser runs."""
    user = await _seed_user(db_session, consent=False)

    result = await analytics_service.record_event(
        db_session,
        event_type=EVENT_PROJECT_CREATED,
        user=user,
        event_data={
            "region": "AT-5",
            # PII key — would be rejected if the consent gate didn't
            # short-circuit. Pin that the gate runs FIRST.
            "user_email": "should-not-leak@example.com",
        },
    )
    await db_session.commit()

    assert result is None
    rows = (
        await db_session.execute(select(UsageAnalyticsEvent))
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# 2. With consent → event persisted, fields sanitised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_persisted_when_consent_true(db_session: AsyncSession):
    """Happy path. Row carries the anonymised id, the matched fields
    survive, the industry_segment is copied through, the region is
    propagated."""
    user = await _seed_user(db_session, consent=True)

    result = await analytics_service.record_event(
        db_session,
        event_type=EVENT_PROJECT_CREATED,
        user=user,
        event_data={"has_plans": True, "region": "AT-5"},
        region_code="AT-5",
    )
    await db_session.commit()

    assert result is not None
    rows = (
        await db_session.execute(select(UsageAnalyticsEvent))
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]

    # Pseudonymised id matches the helper output. Identity check
    # against the raw user_id ensures we DON'T accidentally store
    # the plain UUID.
    assert row.anonymous_user_id == analytics_service.hash_user_id(user.id)
    assert str(user.id) not in row.anonymous_user_id

    # Sanitised event_data carries only whitelisted keys with
    # round-tripped types.
    assert row.event_data == {"has_plans": True, "region": "AT-5"}
    assert row.region_code == "AT-5"
    assert row.industry_segment == "builder"


# ---------------------------------------------------------------------------
# 3. PII keys in event_data → entire event rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_key_rejects_entire_event(db_session: AsyncSession):
    """A field whose name matches a banned-key pattern (email, name,
    user_id, …) causes the sanitiser to raise. ``record_event``
    catches the ValueError, logs, and returns None — the row never
    lands in the DB even partially. Defence in depth against an
    accidental ``{"user_email": ...}`` slip.
    """
    user = await _seed_user(db_session, consent=True)

    result = await analytics_service.record_event(
        db_session,
        event_type=EVENT_PROJECT_CREATED,
        user=user,
        event_data={
            # Allowed key:
            "has_plans": False,
            # Banned key pattern: ``user_id`` matches r"\buser\b" or
            # r"\buid\b" — should reject the whole event.
            "user_id": "abc",
        },
    )
    await db_session.commit()

    assert result is None
    rows = (
        await db_session.execute(select(UsageAnalyticsEvent))
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# 4. Consent toggle on/off changes downstream behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_changes_record_behaviour(db_session: AsyncSession):
    """Same user, three calls: opt-in → recorded; opt-out →
    discarded; opt-back-in → recorded. Locks the consent-gate as
    the source of truth for the record path."""
    user = await _seed_user(db_session, consent=True)

    # Opt-in: row written.
    r1 = await analytics_service.record_event(
        db_session,
        event_type=EVENT_USER_SIGNUP,
        user=user,
        event_data={"industry": "builder"},
    )
    assert r1 is not None

    # Opt-out:
    user.analytics_consent = False
    await db_session.flush()
    r2 = await analytics_service.record_event(
        db_session,
        event_type=EVENT_USER_SIGNUP,
        user=user,
        event_data={"industry": "builder"},
    )
    assert r2 is None

    # Opt-back-in:
    user.analytics_consent = True
    await db_session.flush()
    r3 = await analytics_service.record_event(
        db_session,
        event_type=EVENT_USER_SIGNUP,
        user=user,
        event_data={"industry": "builder"},
    )
    assert r3 is not None

    await db_session.commit()
    rows = (
        await db_session.execute(select(UsageAnalyticsEvent))
    ).scalars().all()
    # Two rows total — opt-out call wrote nothing.
    assert len(rows) == 2
    # Both rows share the same anonymous_user_id (same user, same
    # salt). This is the property that lets the dashboard count
    # distinct users without joining back to the user table.
    assert rows[0].anonymous_user_id == rows[1].anonymous_user_id


# ---------------------------------------------------------------------------
# 5. Pseudonymisation is irreversible
# ---------------------------------------------------------------------------


def test_hash_user_id_is_deterministic_and_salt_dependent(
    monkeypatch,
):
    """Two properties, both critical for DSGVO Art. 4 Nr. 5:

    a) Determinism — same user + same salt → same hash. Required
       so we can correlate events from one user across time.
    b) Salt-dependence — same user + DIFFERENT salt → different
       hash. Required so a leaked DB without the salt can't be
       joined against fresh hashes computed elsewhere.

    The hash is also a 64-char hex string (SHA-256), and the raw
    UUID hex never appears in the output (protection against a
    naïve "I'll just look up the user_id substring" attack)."""
    user_id = uuid.UUID("12345678-1234-1234-1234-123456789012")

    monkeypatch.setattr(settings, "analytics_salt", "salt-A")
    h_a = analytics_service.hash_user_id(user_id)

    # Re-run with the same salt → identical hash.
    h_a_again = analytics_service.hash_user_id(user_id)
    assert h_a == h_a_again, "hash must be deterministic for fixed salt"

    # Different salt → different hash.
    monkeypatch.setattr(settings, "analytics_salt", "salt-B")
    h_b = analytics_service.hash_user_id(user_id)
    assert h_a != h_b, "hash must change when salt changes"

    # Output shape: 64 lower-case hex chars (SHA-256).
    assert len(h_a) == 64 and len(h_b) == 64
    assert all(c in "0123456789abcdef" for c in h_a)
    assert all(c in "0123456789abcdef" for c in h_b)

    # Raw user-id hex must NOT appear in the output — defends
    # against a "raw substring leak" foot-gun.
    assert user_id.hex not in h_a
    assert user_id.hex not in h_b


# ---------------------------------------------------------------------------
# 6. Region helper extracts Bundesland-level codes
# ---------------------------------------------------------------------------


def test_derive_region_code_extracts_bundesland_only():
    """Unit-test the Bundesland heuristic. Town-level addresses
    return None because the regex anchors on Bundesland-level
    keywords; the privacy invariant is that we never emit
    finer-grained location data than ISO 3166-2:AT."""
    assert (
        analytics_service.derive_region_code("Salzburger Straße 12, Salzburg")
        == "AT-5"
    )
    assert (
        analytics_service.derive_region_code("1010 Wien, Stephansplatz 1")
        == "AT-9"
    )
    assert (
        analytics_service.derive_region_code("Innsbruck, Tirol")
        == "AT-7"
    )
    # No Bundesland mention ⇒ None (privacy default).
    assert analytics_service.derive_region_code("Bahnhofstraße 5") is None
    assert analytics_service.derive_region_code(None) is None
    assert analytics_service.derive_region_code("") is None
