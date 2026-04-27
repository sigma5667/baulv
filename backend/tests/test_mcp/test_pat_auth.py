"""Tests for the programmatic-access-token (PAT) auth path.

These cover the helpers in ``app.api_key_auth`` and the
``resolve_principal`` dispatcher in ``app.mcp.principal`` — the two
pieces that turn a Bearer header into a ``User`` for both the
``/api/auth/me/api-keys`` REST surface and the ``/mcp`` SSE handshake.

We don't drive the SSE wire here; that's a manual-QA step against
Claude Desktop. What we *can* lock down in unit tests is the crypto
shape (prefix, hex digest, constant-time comparison) and the
revocation behaviour, both of which are easy to break in subtle ways
on refactor.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_key_auth import (
    PAT_DISPLAY_PREFIX_LEN,
    PAT_SCHEME_PREFIX,
    display_prefix,
    hash_token,
    looks_like_pat,
    mint_token,
    verify_pat,
)
from app.db.models.api_key import ApiKey
from app.db.models.user import User
from app.mcp.principal import resolve_principal


# ---------------------------------------------------------------------------
# Pure-function tests — no DB needed
# ---------------------------------------------------------------------------


def test_mint_token_has_pat_prefix():
    """``mint_token`` must produce a ``pat_``-prefixed string so the
    bearer-header dispatcher can route to the PAT path without
    decoding."""
    token = mint_token()
    assert token.startswith(PAT_SCHEME_PREFIX), token
    # The remainder is a URL-safe base64 token from secrets — at least
    # 32 bytes of entropy → ~43 chars after b64. Anything materially
    # shorter would mean we accidentally regressed the entropy budget.
    assert len(token) >= len(PAT_SCHEME_PREFIX) + 30


def test_mint_token_is_random():
    """Two consecutive mints must not collide. Catches an accidental
    static-seed bug in ``secrets``."""
    assert mint_token() != mint_token()


def test_looks_like_pat_distinguishes_jwt_from_pat():
    """``looks_like_pat`` is the dispatch primitive — JWTs must not
    accidentally take the PAT path."""
    assert looks_like_pat(mint_token()) is True
    # A typical JWT is three base64 segments separated by dots; none
    # start with ``pat_``.
    assert (
        looks_like_pat(
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature"
        )
        is False
    )
    assert looks_like_pat("") is False
    assert looks_like_pat("nonsense") is False


def test_hash_token_is_hex_sha256():
    """Hashes are stored as 64-char hex SHA-256. Anything else means
    the column-length check would silently truncate or reject."""
    token = mint_token()
    digest = hash_token(token)
    assert len(digest) == 64
    int(digest, 16)  # raises if not hex


def test_hash_token_is_deterministic():
    token = mint_token()
    assert hash_token(token) == hash_token(token)


def test_display_prefix_returns_truncated_token():
    """The prefix is what we show in the UI and what we look up by;
    the length needs to stay stable so the DB column and the lookup
    agree."""
    token = mint_token()
    prefix = display_prefix(token)
    assert prefix == token[:PAT_DISPLAY_PREFIX_LEN]
    assert len(prefix) == PAT_DISPLAY_PREFIX_LEN


# ---------------------------------------------------------------------------
# DB-backed tests — verify_pat and resolve_principal
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"agent-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Agent User",
    )
    db.add(user)
    await db.flush()
    return user


async def _mint_and_persist(db: AsyncSession, user: User) -> str:
    """Mint a PAT and write the row the way the ``POST /api-keys``
    endpoint would — but skip the audit log so the test stays focused
    on the auth path."""
    token = mint_token()
    db.add(
        ApiKey(
            user_id=user.id,
            name="test key",
            key_prefix=display_prefix(token),
            key_hash=hash_token(token),
        )
    )
    await db.commit()
    return token


@pytest.mark.asyncio
async def test_verify_pat_returns_user_for_valid_token(db_session: AsyncSession):
    """End-to-end: a freshly minted token resolves back to the
    issuing user."""
    user = await _make_user(db_session)
    token = await _mint_and_persist(db_session, user)

    resolved = await verify_pat(db_session, token)
    assert resolved is not None
    assert resolved.id == user.id


@pytest.mark.asyncio
async def test_verify_pat_rejects_revoked_token(db_session: AsyncSession):
    """Once we set ``revoked_at`` the same plaintext must stop
    authenticating. This is the soft-delete contract the DELETE
    endpoint relies on."""
    from datetime import datetime, timezone

    user = await _make_user(db_session)
    token = await _mint_and_persist(db_session, user)

    # Revoke
    from sqlalchemy import select

    api_key = (
        await db_session.execute(
            select(ApiKey).where(ApiKey.user_id == user.id)
        )
    ).scalars().first()
    assert api_key is not None
    api_key.revoked_at = datetime.now(timezone.utc)
    await db_session.commit()

    resolved = await verify_pat(db_session, token)
    assert resolved is None


@pytest.mark.asyncio
async def test_verify_pat_rejects_tampered_token(db_session: AsyncSession):
    """A token with a valid prefix but wrong body must not authenticate.
    Prevents the prefix-only attack of "I have your prefix, can I
    bruteforce the suffix" — ``secrets.compare_digest`` against the
    full hash protects us."""
    user = await _make_user(db_session)
    token = await _mint_and_persist(db_session, user)
    # Same prefix, different suffix → different hash.
    forged = token[:PAT_DISPLAY_PREFIX_LEN] + secrets.token_urlsafe(32)

    resolved = await verify_pat(db_session, forged)
    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_principal_routes_pat(db_session: AsyncSession):
    """``resolve_principal`` is the bearer dispatcher — a PAT must
    take the PAT branch and end up at the same User as ``verify_pat``."""
    user = await _make_user(db_session)
    token = await _mint_and_persist(db_session, user)

    resolved = await resolve_principal(token, db_session)
    assert resolved is not None
    assert resolved.id == user.id


@pytest.mark.asyncio
async def test_resolve_principal_returns_none_for_garbage(
    db_session: AsyncSession,
):
    """Empty / malformed credentials must return None, never raise.
    The transport layer turns ``None`` into a clean 401; an exception
    here would leak a 500 instead."""
    assert await resolve_principal("", db_session) is None
    assert await resolve_principal("   ", db_session) is None
    # JWT-shaped but unsigned with our key → decode_token returns None
    # → resolve_principal returns None.
    assert (
        await resolve_principal(
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature",
            db_session,
        )
        is None
    )


# ---------------------------------------------------------------------------
# Contextvar mechanism — what the SSE transport relies on
# ---------------------------------------------------------------------------


def test_get_current_user_id_raises_when_not_bound():
    """Calling the helper outside an authenticated context is a
    programmer error — auth runs *before* any tool dispatcher gets
    the chance to touch this. We want a loud ``LookupError`` so the
    bug is impossible to overlook in tests, not a silent ``None`` that
    cascades into a confusing query failure."""
    from app.mcp.principal import get_current_user_id

    with pytest.raises(LookupError):
        get_current_user_id()


def test_contextvar_is_isolated_per_async_task():
    """The whole reason we use a contextvar instead of a module
    global: parallel SSE connections for different users must not
    cross-contaminate. We assert the bind/unbind round-trip preserves
    the value on this task and that ``reset`` returns to the prior
    state, which is what the transport's ``finally`` clause relies on
    for cleanup."""
    from app.mcp.principal import current_user_id_var

    user_id = uuid.uuid4()
    assert current_user_id_var.get() is None
    token = current_user_id_var.set(user_id)
    try:
        assert current_user_id_var.get() == user_id
    finally:
        current_user_id_var.reset(token)
    assert current_user_id_var.get() is None
