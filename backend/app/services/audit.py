"""Audit log helpers.

One public entry point (``log_event``) and a small enum of event
types. Callers pass the FastAPI ``Request`` object so we can
automatically capture the client IP and User-Agent — these are part
of "the circumstances in which the data was processed" that Art. 30
DSGVO expects a controller to be able to reconstruct.

The helper is a fire-and-forget write: if the caller has already
committed their transaction or there's no DB session available, we
log a warning and move on rather than failing the surrounding
request. Audit logging is a *support* function, not a blocker.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditLogEntry

logger = logging.getLogger(__name__)


# Canonical event types. Kept as plain strings (not an Enum) so the
# DB column stays a simple varchar and we can introduce new events
# without migrations. New events should follow the ``noun.verb``
# pattern so ``user.login`` sorts next to ``user.login_failed``.
EVENT_LOGIN = "user.login"
EVENT_LOGIN_FAILED = "user.login_failed"
EVENT_REGISTER = "user.register"
EVENT_PASSWORD_CHANGED = "user.password_changed"
EVENT_DATA_EXPORTED = "user.data_exported"
EVENT_ACCOUNT_DELETED = "user.account_deleted"
EVENT_SESSION_REVOKED = "user.session_revoked"
EVENT_SESSIONS_REVOKED_ALL = "user.sessions_revoked_all"
EVENT_PRIVACY_UPDATED = "user.privacy_updated"


def _client_ip(request: Request | None) -> str | None:
    """Best-effort extraction of the caller's IP.

    Honors ``X-Forwarded-For`` if present (Railway / any reverse proxy
    puts the real client there), falling back to the direct socket
    peer. We deliberately do *not* try to validate the header — if a
    proxy is lying to us, so is our view of the world.
    """
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # XFF can contain a chain of hops; the leftmost is the client.
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    if ua is None:
        return None
    # Trim to the column size. 500 chars is plenty for any real UA and
    # avoids a runtime error on obscure bots with kilobyte-long strings.
    return ua[:500]


async def log_event(
    db: AsyncSession,
    *,
    event_type: str,
    user_id: UUID | None,
    request: Request | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Append a single audit event.

    Must run inside an existing DB session; the caller owns the
    commit. On failure we log and swallow — audit failures must not
    block the user-facing operation.
    """
    try:
        entry = AuditLogEntry(
            user_id=user_id,
            event_type=event_type,
            meta=meta,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
        db.add(entry)
        await db.flush()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to write audit entry event=%s user=%s: %s",
            event_type,
            user_id,
            e,
        )
