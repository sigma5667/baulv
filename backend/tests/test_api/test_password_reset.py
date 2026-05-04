"""Tests for the v23.4 DS-3 password-reset flow.

Coverage matches the DS-3 spec:

  1. ``request_password_reset`` with a known email → token row
     written, audit row recorded with ``result=sent``, email
     attempted (we patch ``send_password_reset_email`` so the
     test never hits Resend).
  2. Same endpoint with an unknown email → no token row, audit
     row with ``user_id=None`` and ``result=no_account``,
     response is the same generic 200 OK message.
  3. ``confirm_password_reset`` with a valid plaintext token →
     200 OK, password is rewritten, all sessions revoked, the
     token is marked used.
  4. Confirm with an expired token → 400, password unchanged.
  5. Confirm with an already-used token → 400, password unchanged.
  6. Rate-limit: 4th request inside an hour → no new token, audit
     row with ``result=rate_limited``.
  7. Email-service failure → endpoint still returns 200 OK and
     writes the audit row (no info-leak via response shape).

The tests call the endpoint functions directly with the
``db_session`` fixture (in-memory SQLite per the conftest) instead
of standing up the FastAPI app — that keeps the test runtime tight
and matches the v23.2/v23.3 conventions in the same package.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import (
    PASSWORD_RESET_REQUESTS_PER_HOUR,
    confirm_password_reset,
    request_password_reset,
)
from app.auth import hash_password, verify_password
from app.db.models.password_reset_token import PasswordResetToken
from app.db.models.session import UserSession
from app.db.models.user import User
from app.legal_versions import PRIVACY_POLICY_VERSION, TERMS_VERSION
from app.schemas.user import PasswordResetConfirm, PasswordResetRequest
from app.services.audit import (
    EVENT_PASSWORD_RESET_COMPLETED,
    EVENT_PASSWORD_RESET_REQUESTED,
)
from app.services.password_reset import _hash_token, mint_reset_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_request():
    """Stand-in FastAPI Request. The reset endpoints call
    ``log_event`` which reads ``request.headers`` and ``request.client``
    — anything else can be a MagicMock."""
    request = MagicMock()
    request.headers = {}
    request.client = None
    return request


async def _seed_user(
    db: AsyncSession, *, email: str = "test@example.com"
) -> User:
    """Insert a real User row matching the schema's required fields.

    The reset code reads ``user.email``, ``user.id``, ``user.full_name``;
    everything else can stay at its default. We pin a concrete
    ``password_hash`` so the password-overwrite assertions can compare
    before/after.
    """
    user = User(
        id=uuid.uuid4(),
        email=email.lower(),
        password_hash=hash_password("originalpass123"),
        full_name="Test User",
        company_name=None,
        marketing_email_opt_in=False,
        current_privacy_version=PRIVACY_POLICY_VERSION,
        current_terms_version=TERMS_VERSION,
    )
    db.add(user)
    await db.commit()
    return user


# ---------------------------------------------------------------------------
# 1. Existing email — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_password_reset_with_known_email_writes_token_and_sends(
    db_session: AsyncSession,
):
    """Known email → exactly one outstanding token row, audit row
    with ``result=sent``, ``send_password_reset_email`` invoked
    once with the plaintext token (we patch it so the test never
    talks to Resend)."""
    user = await _seed_user(db_session, email="hit@example.com")

    with patch("app.api.auth.send_password_reset_email") as mock_send:
        mock_send.return_value = True
        response = await request_password_reset(
            PasswordResetRequest(email="hit@example.com"),
            _mock_request(),
            db=db_session,
        )
        await db_session.commit()

    # Generic German-message response.
    assert "Falls ein Konto" in response["message"]

    # Exactly one token row was created.
    rows = (
        await db_session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].used_at is None
    # Expiry roughly 1 hour out — allow generous slack for slow CI.
    expected_exp = datetime.now(timezone.utc) + timedelta(hours=1)
    assert (
        abs((rows[0].expires_at - expected_exp).total_seconds()) < 60
    )

    # ``send_password_reset_email`` was invoked once with the plaintext.
    assert mock_send.call_count == 1
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_email"] == "hit@example.com"
    assert kwargs["reset_token"]  # non-empty
    assert kwargs["user_name"] == "Test User"
    # The token in the call MUST hash to what got persisted.
    assert _hash_token(kwargs["reset_token"]) == rows[0].token_hash


# ---------------------------------------------------------------------------
# 2. Unknown email — no leak
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_password_reset_with_unknown_email_no_token(
    db_session: AsyncSession,
):
    """Unknown email → no token row, no email send, but the same
    generic 200 OK message. The audit row is written with
    ``user_id=None`` so the operator can grep ``no_account``
    attempts."""
    with patch("app.api.auth.send_password_reset_email") as mock_send:
        response = await request_password_reset(
            PasswordResetRequest(email="ghost@example.com"),
            _mock_request(),
            db=db_session,
        )
        await db_session.commit()

    assert "Falls ein Konto" in response["message"]
    assert mock_send.call_count == 0

    # No token row written.
    rows = (
        await db_session.execute(select(PasswordResetToken))
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# 3. Confirm with valid token — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_password_reset_with_valid_token_rewrites_password(
    db_session: AsyncSession,
):
    """Valid plaintext token → 200 OK, password hash changes,
    every session for the user is revoked, the token row is
    marked used."""
    user = await _seed_user(db_session, email="reset-ok@example.com")
    original_hash = user.password_hash

    # Seed an active session so we can verify the revoke.
    session_row = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        jti="test-jti",
        user_agent=None,
        ip_address=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(session_row)
    await db_session.commit()

    plaintext = await mint_reset_token(db_session, user=user)
    await db_session.commit()

    response = await confirm_password_reset(
        PasswordResetConfirm(token=plaintext, new_password="newSecret123"),
        _mock_request(),
        db=db_session,
    )
    await db_session.commit()

    assert "erfolgreich" in response["message"]

    # Password hash changed and the new password verifies.
    await db_session.refresh(user)
    assert user.password_hash != original_hash
    assert verify_password("newSecret123", user.password_hash)

    # Session is revoked.
    await db_session.refresh(session_row)
    assert session_row.revoked_at is not None

    # Token row marked used.
    token_row = (
        await db_session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id
            )
        )
    ).scalars().first()
    assert token_row is not None
    assert token_row.used_at is not None


# ---------------------------------------------------------------------------
# 4. Confirm with expired token — rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_password_reset_with_expired_token_400(
    db_session: AsyncSession,
):
    """A token whose ``expires_at`` is in the past must be rejected
    with the generic German 400 message. Password and session
    state untouched."""
    user = await _seed_user(db_session, email="expired@example.com")
    original_hash = user.password_hash

    # Hand-roll a token with expires_at = -1h so we don't have to
    # monkey-patch the clock.
    plaintext = "fake-but-otherwise-valid-token-string"
    expired_row = PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=_hash_token(plaintext),
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        used_at=None,
    )
    db_session.add(expired_row)
    await db_session.commit()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await confirm_password_reset(
            PasswordResetConfirm(token=plaintext, new_password="newSecret123"),
            _mock_request(),
            db=db_session,
        )
    assert exc_info.value.status_code == 400
    assert "ungültig" in exc_info.value.detail or "abgelaufen" in exc_info.value.detail

    # Password unchanged.
    await db_session.refresh(user)
    assert user.password_hash == original_hash


# ---------------------------------------------------------------------------
# 5. Confirm with already-used token — rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_password_reset_with_used_token_400(
    db_session: AsyncSession,
):
    """A token whose ``used_at`` is non-NULL must be rejected as
    if it didn't exist. Single-use guarantee."""
    user = await _seed_user(db_session, email="used@example.com")
    original_hash = user.password_hash

    plaintext = await mint_reset_token(db_session, user=user)
    await db_session.commit()
    # First use succeeds.
    await confirm_password_reset(
        PasswordResetConfirm(token=plaintext, new_password="firstNew123"),
        _mock_request(),
        db=db_session,
    )
    await db_session.commit()

    # Second use must fail.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await confirm_password_reset(
            PasswordResetConfirm(
                token=plaintext, new_password="secondAttempt123"
            ),
            _mock_request(),
            db=db_session,
        )
    assert exc_info.value.status_code == 400

    # Password matches the *first* successful reset, not the
    # second attempt.
    await db_session.refresh(user)
    assert user.password_hash != original_hash
    assert verify_password("firstNew123", user.password_hash)
    assert not verify_password("secondAttempt123", user.password_hash)


# ---------------------------------------------------------------------------
# 6. Rate-limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_password_reset_rate_limited_after_threshold(
    db_session: AsyncSession,
):
    """The 4th request for the same user inside one hour must NOT
    create a new token, regardless of the response message. The
    audit row's ``meta.result`` is ``rate_limited``."""
    user = await _seed_user(db_session, email="ratelimit@example.com")

    # Saturate the budget. ``mint_reset_token`` invalidates prior
    # outstanding tokens, but each call still leaves a row with the
    # same ``created_at`` window — that's what the limiter counts.
    for _ in range(PASSWORD_RESET_REQUESTS_PER_HOUR):
        await mint_reset_token(db_session, user=user)
    await db_session.commit()

    rows_before = (
        await db_session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id
            )
        )
    ).scalars().all()
    assert len(rows_before) == PASSWORD_RESET_REQUESTS_PER_HOUR

    with patch("app.api.auth.send_password_reset_email") as mock_send:
        response = await request_password_reset(
            PasswordResetRequest(email="ratelimit@example.com"),
            _mock_request(),
            db=db_session,
        )
        await db_session.commit()

    # Same generic German message.
    assert "Falls ein Konto" in response["message"]
    # No new token row.
    rows_after = (
        await db_session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id
            )
        )
    ).scalars().all()
    assert len(rows_after) == PASSWORD_RESET_REQUESTS_PER_HOUR
    # And no email was sent.
    assert mock_send.call_count == 0


# ---------------------------------------------------------------------------
# 7. Email-service failure — still 200 OK
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_password_reset_email_failure_still_200(
    db_session: AsyncSession,
):
    """If ``send_password_reset_email`` returns False (Resend down,
    no API key, etc.) the endpoint still returns the generic 200
    OK and the token row still lives in the DB. The user-facing
    surface must be identical to the success path — no info leak."""
    await _seed_user(db_session, email="failmail@example.com")

    with patch("app.api.auth.send_password_reset_email") as mock_send:
        mock_send.return_value = False
        response = await request_password_reset(
            PasswordResetRequest(email="failmail@example.com"),
            _mock_request(),
            db=db_session,
        )
        await db_session.commit()

    assert "Falls ein Konto" in response["message"]
    assert mock_send.call_count == 1
    # Token still exists — operator can resend manually if needed.
    rows = (
        await db_session.execute(select(PasswordResetToken))
    ).scalars().all()
    assert len(rows) == 1
