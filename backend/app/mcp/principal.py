"""Per-task authenticated principal for MCP tool handlers.

The MCP server's tool dispatcher runs inside the same async task that
holds the SSE connection. We set a contextvar on that task at handshake
time, after Bearer auth has succeeded, and tool handlers read it back
out to know which user they're acting for.

Why a contextvar (and not a closure)
====================================

The MCP ``Server`` is a process-singleton — we register tools on it
once at import time, before any user has connected. We can't bake the
user into a closure on those handlers without either rebuilding the
server per connection (defeats the SDK's design) or sharing a mutable
"current user" attribute between concurrent connections (race
condition).

Contextvars give us isolated per-task state for free, with the bonus
that ``asyncio.create_task`` propagates the binding to spawned tasks
under the same SSE handler.

Two-credential resolution
=========================

``resolve_principal`` accepts either a JWT or a PAT in the same
``Authorization: Bearer`` header. We dispatch on the prefix: PATs
unambiguously start with ``pat_``; everything else gets the JWT path.
Bad credentials always return ``None`` — we never raise here so the
helper is reusable from non-FastAPI contexts.
"""

from __future__ import annotations

import contextvars
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_key_auth import looks_like_pat, verify_pat
from app.auth import decode_token
from app.db.models.session import UserSession
from app.db.models.user import User


# Per-task user_id binding. Tools dereference this to fetch the User
# (or just to scope queries by user_id without a redundant DB round
# trip). Reset at the end of each request to avoid leakage between
# connections that happen to reuse the same task.
current_user_id_var: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "mcp_current_user_id",
    default=None,
)


def get_current_user_id() -> UUID:
    """Read the authenticated user_id from the surrounding context.

    Raises ``LookupError`` if called outside an authenticated request —
    that's a programmer error, not a runtime auth failure (auth is
    handled before the tool dispatcher is reached).
    """
    user_id = current_user_id_var.get()
    if user_id is None:
        raise LookupError(
            "MCP tool handler called without an authenticated principal — "
            "did the auth middleware run?"
        )
    return user_id


async def resolve_principal(
    token: str,
    db: AsyncSession,
) -> User | None:
    """Resolve a Bearer credential to a ``User``.

    Returns ``None`` for any failure (bad scheme, signature, expiry,
    revocation). Never raises. The caller decides whether to translate
    that into a 401.
    """
    token = token.strip()
    if not token:
        return None

    if looks_like_pat(token):
        return await verify_pat(db, token)

    # JWT path — same machinery as the SPA. We deliberately reuse the
    # session-revocation check here so a "log out everywhere" performed
    # via the SPA also kills any agent that somehow inherited the JWT.
    payload = decode_token(token)
    if payload is None:
        return None
    jti = payload.get("jti")
    sub = payload.get("sub")
    if not jti or not sub:
        return None

    try:
        user_id = UUID(sub)
    except ValueError:
        return None

    session_row = (
        await db.execute(select(UserSession).where(UserSession.jti == jti))
    ).scalars().first()
    if session_row is None or session_row.revoked_at is not None:
        return None
    if session_row.user_id != user_id:
        return None

    return await db.get(User, user_id)
