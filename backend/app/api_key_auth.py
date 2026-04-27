"""Programmatic-access-token (PAT) helpers.

This module is **separate from ``app.auth``** so the JWT/session code
stays focused on interactive auth. The two paths are independent —
neither one falls back to the other inside this file.

Token format
============

    pat_<43 url-safe base64 characters>

* The ``pat_`` prefix announces the credential class. Anyone scanning
  for accidental leaks (in logs, in PR diffs, in error messages) can
  spot it on sight, the same way ``sk_`` does for Stripe keys or
  ``ghp_`` for GitHub PATs.
* The payload is 32 random bytes encoded url-safe (no ``=`` padding).
  256 bits of entropy — well past any brute-force concern even without
  bcrypt's slow-path defence.

Stored shape
============

* ``key_prefix`` — first 12 characters of the full token, including
  the ``pat_`` scheme. Surfaced in the UI as a recognisable handle and
  used as the indexed lookup column.
* ``key_hash`` — SHA-256 hex of the full token (64 chars).

Verification on each request
============================

1. Fast-fail if the credential doesn't start with ``pat_``.
2. Pull the prefix (first 12 chars), look up by indexed column.
3. SHA-256 the presented token, compare in constant time
   (``secrets.compare_digest``) against the stored hash.
4. Reject if revoked.
5. Update ``last_used_at`` (best-effort — failures here don't fail
   the request).

The bcrypt-vs-SHA-256 question
==============================

We chose SHA-256 over bcrypt because:

* Tokens are high-entropy machine-generated random bytes, not
  human-chosen passwords. Bcrypt's slow-hash defence buys nothing
  against a 256-bit secret.
* PATs are presented on **every** request from a hot agent loop —
  bcrypt at default work factor (~250 ms) would dominate request
  latency and be a DoS vector for n8n schedulers that hammer one tool.
* Industry precedent: GitHub, Anthropic, Stripe, Vercel, Cloudflare
  all use SHA-256 (or SHA-512) for PAT verification.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.api_key import ApiKey
from app.db.models.user import User


PAT_SCHEME_PREFIX = "pat_"
# How much of the token we keep visible / store as the lookup column.
# Long enough that prefix collisions across users are vanishingly
# improbable, short enough that the user can recognise their key in a
# list at a glance.
PAT_DISPLAY_PREFIX_LEN = 12


def mint_token() -> str:
    """Generate a fresh PAT.

    Returns the plaintext token. The caller is expected to compute
    ``key_prefix`` / ``key_hash`` immediately and persist them, then
    forget the plaintext.
    """
    body = secrets.token_urlsafe(32)
    return f"{PAT_SCHEME_PREFIX}{body}"


def hash_token(token: str) -> str:
    """SHA-256 hex of the full token. 64 chars, lowercase."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def display_prefix(token: str) -> str:
    """First N chars of the token — including the ``pat_`` scheme — for
    storage as ``key_prefix`` and surfacing in the UI.
    """
    return token[:PAT_DISPLAY_PREFIX_LEN]


def looks_like_pat(credential: str) -> bool:
    """Quick syntactic check used by the dual-credential resolver to
    route a Bearer header to the PAT path vs. the JWT path. JWTs
    contain dots; PATs don't (they're URL-safe base64). The scheme
    prefix is the unambiguous tell.
    """
    return credential.startswith(PAT_SCHEME_PREFIX)


async def verify_pat(
    db: AsyncSession,
    presented_token: str,
) -> User | None:
    """Resolve a presented PAT to its owning ``User``.

    Returns ``None`` on **any** failure (bad scheme, no matching prefix,
    hash mismatch, revoked). The caller is expected to translate that
    into 401 — we don't raise here so this function can also be used
    in optional-auth contexts.

    Side effect on success: ``ApiKey.last_used_at`` is set to "now".
    The flush is left to the surrounding transaction; we don't commit
    inside this helper.
    """
    if not looks_like_pat(presented_token):
        return None

    prefix = display_prefix(presented_token)
    presented_hash = hash_token(presented_token)

    # Index hit — usually exactly one row. We loop only as defence in
    # depth against (extremely improbable) prefix collisions across
    # users.
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_prefix == prefix)
    )
    candidates = result.scalars().all()

    matched: ApiKey | None = None
    for candidate in candidates:
        # ``compare_digest`` is constant-time; using ``==`` here would
        # in theory leak hash bytes via timing. Both arguments are
        # 64-char hex strings.
        if secrets.compare_digest(candidate.key_hash, presented_hash):
            matched = candidate
            break

    if matched is None:
        return None
    if matched.revoked_at is not None:
        return None

    user = await db.get(User, matched.user_id)
    if user is None:
        # Defensive — FK is ON DELETE CASCADE, so a live ApiKey row
        # with a missing user shouldn't exist. Treat it as a soft
        # auth-fail rather than a 500.
        return None

    matched.last_used_at = datetime.now(timezone.utc)
    await db.flush()
    return user
