"""Admin-only operations.

Gated behind a settings allow-list (``ADMIN_EMAILS`` env var) — by
default every endpoint here returns 403, so a fresh deploy ships
locked-down. To enable, set ``ADMIN_EMAILS=tobi@baulv.at`` (or
multiple comma-separated) on the Railway service.

Why an email allow-list rather than a separate admin-token flow:
the existing JWT auth already gives us per-request user identity,
audit-log entries, IP capture, and session revocation. Layering
*another* secret on top of that buys nothing for a single-tenant
operator scenario; it just creates one more thing to leak.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.db.models.user import User
from app.db.session import get_db
from app.services.audit_cleanup import run_all_cleanups

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Auth gate for everything in this module.

    The user must be authenticated *and* their email must appear in
    the ``ADMIN_EMAILS`` allow-list. We deliberately don't grant
    admin access to anyone if the env var is empty — failing closed
    is the only safe default for "drop tables"-class operations.
    """
    allow = settings.admin_email_list
    if not allow:
        # Setting unset entirely. Treat the whole admin surface as
        # disabled; surface a 403 rather than 503 because we don't
        # want to advertise the existence of admin endpoints to
        # an unauthenticated probe.
        raise HTTPException(
            status_code=403, detail="Admin-Zugriff erforderlich."
        )
    if user.email.strip().lower() not in allow:
        raise HTTPException(
            status_code=403, detail="Admin-Zugriff erforderlich."
        )
    return user


@router.post("/cleanup-audit-logs")
async def trigger_audit_cleanup(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Run the DSGVO Art. 5(1)(e) audit-log retention cleanup now.

    Same code path as the nightly background loop in
    ``app/main.py``'s ``lifespan`` — call it for testing, after a
    bulk-import that created suspicious entries, or to verify the
    cleanup logic before relying on the scheduled run.

    Returns the number of rows deleted from each table. The
    structured ``dsgvo.cleanup`` log line still fires regardless,
    so the run is reflected in the canonical operations log.
    """
    logger.info(
        "admin.audit_cleanup_triggered_by user=%s email=%s",
        admin.id, admin.email,
    )
    result = await run_all_cleanups(db)
    return {
        "audit_log_deleted": result.audit_log_deleted,
        "mcp_audit_log_deleted": result.mcp_audit_log_deleted,
        "total": result.total,
    }
