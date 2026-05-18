"""Public beta-access gate.

While baulv.at is in the closed-beta phase, the marketing surface
(LandingPage, /login, /register, /api-pricing, /developers, …) is
hidden behind a single-page Coming-Soon gate that asks for an access
code. This module is the server side of that flow.

Two endpoints, both unauthenticated:

* ``POST /api/beta/verify`` — accepts ``{code: str}``, constant-time
  compares against ``settings.beta_access_code``. On match it returns
  a 30-day HMAC-signed token; on mismatch returns 401. Rate-limited
  to 10 attempts per minute per client-IP so brute-force is
  impractical even for a short alphanumeric code.

* ``GET /api/beta/status`` — validates a token sent via the
  ``X-Beta-Token`` header. Returns 200 if the signature still
  matches the *current* ``BETA_ACCESS_CODE`` and the token has not
  expired, 401 otherwise. The SPA polls this once on every page-load
  so a Railway-side code rotation invalidates existing sessions
  without needing a server-push channel.

Why the signature includes the access code
==========================================

The HMAC payload is ``{expires_at}:{beta_access_code}``. Rotating
``BETA_ACCESS_CODE`` in Railway therefore invalidates every
outstanding token on its next ``/status`` call — the signature
won't re-derive against the new code. This is the rotation
behaviour Tobi asked for: on a code leak, change the env var and
every tester is kicked back to the gate at their next page-load,
no manual session-wipe needed.

Why **not** an HttpOnly cookie
==============================

The gate is a soft UI cover, not a security boundary — real auth
continues to flow through the JWT on every ``/api/*`` call. Storing
the token in localStorage keeps the frontend code symmetrical with
the existing ``baulv_token`` and avoids a cookie-roundtrip on every
page-load just to know whether to render the gate.

Fail-safe behaviour
===================

If ``BETA_ACCESS_CODE`` is unset (the default), every ``/verify``
submission returns 401 (no code matches the empty string) and every
``/status`` check returns 401 (no token can be signed against an
empty code). A deploy that forgets to set the env var locks every
user out — much safer than the inverse (everyone walks through an
unlocked gate).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Rate limiter — 10 verify attempts per IP per minute
# ---------------------------------------------------------------------------
#
# Same in-memory deque pattern as ``app.api.support_chat``. Single-
# process Railway deploys are fine; if we ever scale horizontally,
# swap for a Redis token-bucket using the same ``check_rate_limit``
# contract.

RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60  # seconds

_ip_attempts: dict[str, deque[float]] = defaultdict(deque)
_ip_lock = Lock()


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Railway sets ``X-Forwarded-For``."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # First entry is the original client; rest are proxies.
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Return ``(allowed, retry_after_seconds)``. Sliding window per IP."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    with _ip_lock:
        q = _ip_attempts[ip]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= RATE_LIMIT_MAX:
            retry_after = int(q[0] + RATE_LIMIT_WINDOW - now) + 1
            return False, max(retry_after, 1)
        q.append(now)
        return True, 0


def _reset_rate_limit() -> None:
    """Test-only hook to wipe rate-limit state between cases."""
    with _ip_lock:
        _ip_attempts.clear()


# ---------------------------------------------------------------------------
# Token issuance + validation
# ---------------------------------------------------------------------------

TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _sign(expires_at: int, access_code: str) -> str:
    """HMAC-SHA256 over ``"{expires_at}:{access_code}"`` keyed with JWT_SECRET.

    Including ``access_code`` in the signed material is the rotation
    primitive: bumping ``BETA_ACCESS_CODE`` in Railway breaks the
    signature on every outstanding token, so the next ``/status``
    call returns 401 and the frontend wipes localStorage.
    """
    payload = f"{expires_at}:{access_code}".encode("utf-8")
    key = settings.jwt_secret.encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _issue_token() -> str:
    """Build a freshly signed token. Format: ``<expires_at>.<hex_sig>``."""
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    sig = _sign(expires_at, settings.beta_access_code)
    return f"{expires_at}.{sig}"


def _verify_token(token: str) -> bool:
    """Return True iff the token is well-formed, not expired, and was
    signed for the *current* ``BETA_ACCESS_CODE``.

    Empty or unset ``BETA_ACCESS_CODE`` disables the gate entirely —
    every token is rejected. That's the fail-safe state for a deploy
    where the env var is missing.
    """
    if not settings.beta_access_code:
        return False
    if not token or "." not in token:
        return False
    expires_str, sig = token.split(".", 1)
    try:
        expires_at = int(expires_str)
    except ValueError:
        return False
    if expires_at <= int(time.time()):
        return False
    expected = _sign(expires_at, settings.beta_access_code)
    # Constant-time compare — defeats timing side-channels on the
    # signature byte-by-byte.
    return hmac.compare_digest(sig, expected)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BetaVerifyRequest(BaseModel):
    # Cap at 256 — a real code is well under that; bounding the input
    # length keeps the constant-time compare loop O(short).
    code: str = Field(min_length=1, max_length=256)


class BetaVerifyResponse(BaseModel):
    token: str
    expires_in: int


class BetaStatusResponse(BaseModel):
    valid: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/verify", response_model=BetaVerifyResponse)
async def verify_beta_code(
    payload: BetaVerifyRequest, request: Request
) -> BetaVerifyResponse:
    """Exchange a beta-access code for a 30-day signed token.

    On a successful match the returned ``token`` should be stored in
    ``localStorage['baulv_beta_session']`` by the SPA. The token is
    only consulted by the SPA's own gate-render decision — the API
    endpoints themselves don't check it (JWT remains the real auth
    boundary on every protected route).
    """
    ip = _client_ip(request)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed:
        logger.warning("beta_verify.rate_limited ip=%s retry=%ds", ip, retry_after)
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele Versuche. Bitte warten Sie {retry_after} Sekunden.",
            headers={"Retry-After": str(retry_after)},
        )

    if not settings.beta_access_code:
        # Fail-safe: an unconfigured gate rejects every submission.
        # Identical 401 to the wrong-code case so an attacker can't
        # probe whether the env var is set just from the response.
        logger.warning("beta_verify.no_code_configured ip=%s", ip)
        raise HTTPException(status_code=401, detail="Code ungültig")

    # Constant-time compare so the wrong-prefix and right-prefix
    # cases take the same time. Without this, an attacker who could
    # measure response time precisely could brute-force the code
    # byte-by-byte instead of all-at-once.
    if not secrets.compare_digest(payload.code, settings.beta_access_code):
        logger.info("beta_verify.bad_code ip=%s", ip)
        raise HTTPException(status_code=401, detail="Code ungültig")

    token = _issue_token()
    logger.info("beta_verify.ok ip=%s", ip)
    return BetaVerifyResponse(token=token, expires_in=TOKEN_TTL_SECONDS)


@router.get("/status", response_model=BetaStatusResponse)
async def beta_status(
    x_beta_token: str | None = Header(default=None),
) -> BetaStatusResponse:
    """Validate a stored token against the *current* ``BETA_ACCESS_CODE``.

    Returns 200 + ``{valid: true}`` only when all of:

    * a token was supplied via the ``X-Beta-Token`` header,
    * the gate is configured (env var non-empty),
    * the expiry has not elapsed, and
    * the HMAC re-derives with the **current** code.

    Returns 401 in every other case. The frontend treats 401 as
    "wipe localStorage, render the gate again". A code rotation in
    Railway therefore propagates to every active tester within one
    page-load.
    """
    if x_beta_token is None or not _verify_token(x_beta_token):
        raise HTTPException(
            status_code=401, detail="Token ungültig oder abgelaufen"
        )
    return BetaStatusResponse(valid=True)
