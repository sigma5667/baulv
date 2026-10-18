"""Admin-only operations.

Gated behind the persistent ``users.is_admin`` flag — by default
every endpoint here returns 403, so a fresh deploy ships
locked-down. The flag can only be set server-side (SQL / migration);
there is no API that writes it.

Security-Fix (Review 2026-10): bis dahin entschied eine
E-Mail-Allowlist (``ADMIN_EMAILS``). Weil die Registrierung nicht
prüft, ob jemand die Adresse besitzt, war das übernehmbar. Migration
029 hat die zum Deploy-Zeitpunkt existierenden Konten der Liste
einmalig auf ``is_admin=TRUE`` gesetzt; danach wird die Liste nicht
mehr ausgewertet.

Why a flag on the user rather than a separate admin-token flow:
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
from app.db.models.user import User
from app.db.session import get_db
from app.services.audit_cleanup import run_all_cleanups

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Auth gate for everything in this module.

    The user must be authenticated *and* carry the persistent
    ``users.is_admin`` flag. Fails closed for everyone else.

    Security-Fix (Review 2026-10): früher entschied allein die
    E-Mail-Adresse (``ADMIN_EMAILS``). Da die Registrierung nicht
    prüft, ob jemand die Adresse besitzt, hätte ein Fremder eine
    gelistete, noch unregistrierte Admin-Adresse registrieren und
    sofort Admin sein können. Das Flag lässt sich nur serverseitig
    setzen (Migration 029 hat bestehende Admin-Konten übernommen).
    """
    if not getattr(user, "is_admin", False):
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
        "waitlist_pending_deleted": result.waitlist_pending_deleted,
        "total": result.total,
    }
