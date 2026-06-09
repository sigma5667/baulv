"""Tests für v24.4.8 — Unternehmer-Bestätigung (B2B-Abgrenzung gegen FAGG/KSchG).

Drei thematische Blöcke:

  1. **Schema-Layer** — UserRegister verlangt jetzt zwingend
     ``company_name`` UND ``accepted_business_terms_version``.
     Beide fehlend → Pydantic ValidationError vor jedem Endpoint.

  2. **Registrierungs-Endpoint** — Stale-Tab-Schutz analog
     Privacy/Terms (409 bei Version-Mismatch); Success-Pfad setzt
     ``user.current_business_terms_version`` UND schreibt einen
     Registrations-Snapshot mit dem Versions-String.

  3. **Stripe-Checkout-Endpoint** — Server-side Enforcement
     (NICHT auf Client-State vertrauen). Drei Pfade:
       * grandfathered user (NULL)            → 400
       * user mit veralteter Version          → 400 (monkeypatch
                                                 simuliert
                                                 BUSINESS_TERMS_VERSION-
                                                 Bump nach Registrierung)
       * user mit aktueller Version           → success + frischer
                                                 business_status_confirmed-
                                                 Snapshot
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import register
from app.api.stripe_api import create_checkout_session
from app.db.models.consent import (
    EVENT_BUSINESS_STATUS_CONFIRMED,
    EVENT_REGISTRATION,
    ConsentSnapshot,
)
from app.db.models.user import User
from app.legal_versions import (
    BUSINESS_TERMS_VERSION,
    PRIVACY_POLICY_VERSION,
    TERMS_VERSION,
)
from app.schemas.user import UserRegister


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_request(*, ip: str | None = None, user_agent: str | None = None):
    """Stand-in for the FastAPI Request used by the consent helpers."""
    request = MagicMock()
    headers: dict[str, str] = {}
    if user_agent:
        headers["user-agent"] = user_agent
    if ip:
        headers["x-forwarded-for"] = ip
    request.headers = headers
    request.client = None
    return request


def _make_payload(
    *,
    email: str | None = None,
    accepted_business_terms_version: str = BUSINESS_TERMS_VERSION,
    company_name: str = "Tester GmbH",
) -> UserRegister:
    """Build a valid v24.4.8 UserRegister payload with overridable parts."""
    return UserRegister(
        email=email or f"bt-{uuid.uuid4()}@example.com",
        password="strongpass123",
        full_name="B2B Tester",
        company_name=company_name,
        accepted_privacy_version=PRIVACY_POLICY_VERSION,
        accepted_terms_version=TERMS_VERSION,
        accepted_business_terms_version=accepted_business_terms_version,
    )


# ---------------------------------------------------------------------------
# Block 1 — Schema-Layer
# ---------------------------------------------------------------------------


def test_register_payload_requires_company_name():
    """v24.4.8: ``company_name`` ist Pflicht (war optional). B2B-only
    Angebot → ohne Firmenname kein UGB-Signal."""
    with pytest.raises(Exception):  # pydantic ValidationError
        UserRegister(
            email="x@example.com",
            password="strongpass123",
            full_name="Test",
            # company_name missing
            accepted_privacy_version=PRIVACY_POLICY_VERSION,
            accepted_terms_version=TERMS_VERSION,
            accepted_business_terms_version=BUSINESS_TERMS_VERSION,
        )


def test_register_payload_requires_business_terms_version():
    """v24.4.8: ``accepted_business_terms_version`` ist Pflicht. Ohne
    sie keine valide Registrierung — Frontend muss die Version aus
    /api/legal/versions holen und mitsenden."""
    with pytest.raises(Exception):  # pydantic ValidationError
        UserRegister(
            email="x@example.com",
            password="strongpass123",
            full_name="Test",
            company_name="Test GmbH",
            accepted_privacy_version=PRIVACY_POLICY_VERSION,
            accepted_terms_version=TERMS_VERSION,
            # accepted_business_terms_version missing
        )


# ---------------------------------------------------------------------------
# Block 2 — Registrierungs-Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_rejects_stale_business_terms_version(
    db_session: AsyncSession,
):
    """Stale-Tab-Schutz: Frontend hat eine ältere
    BUSINESS_TERMS_VERSION gesendet → 409 mit klarem Hinweis."""
    payload = _make_payload(accepted_business_terms_version="0.99")

    with pytest.raises(HTTPException) as exc_info:
        await register(payload, _mock_request(), db=db_session)

    assert exc_info.value.status_code == 409
    assert "Unternehmer" in exc_info.value.detail


@pytest.mark.asyncio
async def test_register_success_sets_business_terms_version_and_writes_snapshot(
    db_session: AsyncSession,
):
    """Happy Path: User wird angelegt mit
    ``current_business_terms_version == BUSINESS_TERMS_VERSION`` UND
    der Registrations-Snapshot trägt das Feld."""
    payload = _make_payload()

    await register(
        payload, _mock_request(ip="203.0.113.99", user_agent="ua/b2b"),
        db=db_session,
    )
    await db_session.commit()

    user = (
        await db_session.execute(
            select(User).where(User.email == payload.email.lower().strip())
        )
    ).scalars().first()
    assert user is not None
    assert user.current_business_terms_version == BUSINESS_TERMS_VERSION

    snapshots = (
        await db_session.execute(
            select(ConsentSnapshot).where(ConsentSnapshot.user_id == user.id)
        )
    ).scalars().all()
    # Genau EIN Snapshot bei Registrierung (event_type=registration),
    # mit allen drei Versionen drin — der business_status_confirmed-
    # Snapshot kommt erst beim Stripe-Checkout dazu.
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.event_type == EVENT_REGISTRATION
    assert snap.privacy_version == PRIVACY_POLICY_VERSION
    assert snap.terms_version == TERMS_VERSION
    assert snap.business_terms_version == BUSINESS_TERMS_VERSION
    assert snap.ip_address == "203.0.113.99"


# ---------------------------------------------------------------------------
# Block 3 — Stripe-Checkout-Endpoint (B2B-Server-Enforcement)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_stripe(monkeypatch):
    """Replace ``stripe.checkout.Session.create`` with a deterministic
    stub that just returns an object carrying the metadata we passed.

    The tests then assert on that captured metadata to verify the
    Dispute-Defense-Felder (business_terms_version,
    business_status_confirmed_at) drinstehen.
    """
    captured: dict = {}

    def _fake_session_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://stripe.test/checkout/xyz")

    monkeypatch.setattr(
        "app.api.stripe_api.stripe.checkout.Session.create",
        _fake_session_create,
    )
    # Stripe.Customer.create wird über _ensure_stripe_customer
    # aufgerufen wenn der User noch keine stripe_customer_id hat.
    # Wir geben dem Test-User direkt eine, damit der Pfad nicht
    # über _ensure_stripe_customer geht.
    return captured


@pytest.fixture
def configured_stripe(monkeypatch):
    """Setzt einen Dummy-Stripe-Key + Price-IDs, damit die
    Pre-Checks in ``create_checkout_session`` durchgehen. Ohne dies
    würde der Endpoint mit 503 abbrechen."""
    from app.api import stripe_api
    from app.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")
    monkeypatch.setattr(settings, "stripe_price_basis", "price_basis_test")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    monkeypatch.setattr(
        stripe_api, "PRICE_MAP",
        {"basis": "price_basis_test", "pro": "price_pro_test"},
    )


async def _seed_user(
    db: AsyncSession,
    *,
    current_business_terms_version: str | None = BUSINESS_TERMS_VERSION,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"chk-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Checkout Tester",
        company_name="Checkout GmbH",
        marketing_email_opt_in=False,
        current_privacy_version=PRIVACY_POLICY_VERSION,
        current_terms_version=TERMS_VERSION,
        current_business_terms_version=current_business_terms_version,
        # Already has a stripe customer id so we don't need to mock
        # stripe.Customer.create for the happy-path tests.
        stripe_customer_id="cus_test_dummy",
    )
    db.add(user)
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_checkout_rejects_grandfathered_user(
    db_session: AsyncSession,
    configured_stripe,
    stub_stripe,
):
    """Bestandsuser (pre-v24.4.8, current_business_terms_version=NULL)
    klickt auf "Jetzt upgraden" → 400 mit klarem Hinweis, dass die
    Bestätigung erforderlich ist. Frontend zeigt dann den
    ConsentRefreshModal."""
    user = await _seed_user(
        db_session, current_business_terms_version=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_checkout_session(
            plan="pro",
            request=_mock_request(),
            user=user,
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert "Unternehmer" in exc_info.value.detail


@pytest.mark.asyncio
async def test_checkout_rejects_stale_business_terms_version(
    db_session: AsyncSession,
    configured_stripe,
    stub_stripe,
    monkeypatch,
):
    """User hatte bei Registrierung "1.0" bestätigt; danach bumpt
    BUSINESS_TERMS_VERSION auf "1.1" (Anwalt-Review). Beim nächsten
    Checkout muss der User refrishen → 400."""
    # User mit der "alten" v1.0-Version.
    user = await _seed_user(
        db_session, current_business_terms_version=BUSINESS_TERMS_VERSION,
    )
    # Simulate the version bump by monkeypatching the constant the
    # endpoint reads. ``app.api.stripe_api`` imported the constant
    # at module load, so we patch it there.
    monkeypatch.setattr(
        "app.api.stripe_api.BUSINESS_TERMS_VERSION",
        "1.1-after-anwaltsreview",
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_checkout_session(
            plan="pro",
            request=_mock_request(),
            user=user,
            db=db_session,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_checkout_success_writes_fresh_business_snapshot(
    db_session: AsyncSession,
    configured_stripe,
    stub_stripe,
):
    """Happy Path: User mit aktueller Bestätigung klickt Upgrade →
    es wird ein frischer business_status_confirmed-Snapshot mit
    IP/UA des Requests geschrieben, UND die Stripe-Session-Metadata
    enthält business_terms_version + business_status_confirmed_at."""
    user = await _seed_user(db_session)

    response = await create_checkout_session(
        plan="pro",
        request=_mock_request(ip="198.51.100.55", user_agent="ua/checkout"),
        user=user,
        db=db_session,
    )
    await db_session.commit()

    assert response == {"checkout_url": "https://stripe.test/checkout/xyz"}

    # Snapshot landed?
    snapshots = (
        await db_session.execute(
            select(ConsentSnapshot)
            .where(ConsentSnapshot.user_id == user.id)
            .where(ConsentSnapshot.event_type == EVENT_BUSINESS_STATUS_CONFIRMED)
        )
    ).scalars().all()
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.business_terms_version == BUSINESS_TERMS_VERSION
    assert snap.ip_address == "198.51.100.55"
    assert snap.user_agent == "ua/checkout"

    # Stripe-Metadata: dispute-defense fields landed in the
    # Session.create call we captured via the stub fixture.
    metadata = stub_stripe.get("metadata", {})
    assert metadata.get("user_id") == str(user.id)
    assert metadata.get("business_terms_version") == BUSINESS_TERMS_VERSION
    # ISO-8601 timestamp; we only verify shape, not exact value.
    confirmed_at = metadata.get("business_status_confirmed_at")
    assert confirmed_at is not None
    # ISO parseable? (datetime.fromisoformat handles 2026-06-09T13:25:11+00:00)
    assert datetime.fromisoformat(confirmed_at) is not None
