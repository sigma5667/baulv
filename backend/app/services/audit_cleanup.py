"""DSGVO Art. 5(1)(e) — periodic deletion of stale audit-log rows.

Two tables get pruned, both 24 months old:

  * ``audit_log_entries``      (canonical user-facing audit log:
                                login, register, password change,
                                privacy update, plan deletion, …)
  * ``mcp_audit_log_entries``  (per-PAT MCP tool dispatch trail)

``consent_snapshots`` is exempt — DSGVO Art. 7 Abs. 1 requires the
controller to be able to demonstrate consent for as long as it's
relevant, which directly opposes the storage-limitation principle
for *those* rows. The cleanup service just doesn't touch the
table.

Run modes
=========

* Background task started in ``app/main.py``'s ``lifespan`` —
  fires once a day at 03:00 UTC. Failures log + retry next day,
  never crash the app.
* Manual trigger via ``POST /api/admin/cleanup-audit-logs`` for
  testing and emergency drains.

Both paths converge on ``run_all_cleanups()``, which logs a
single structured line summarising the rows deleted across both
tables. That's the metric DSGVO compliance reviews want.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditLogEntry
from app.db.models.mcp_audit import McpAuditLogEntry
from app.db.session import async_session_factory
from app.dsgvo_retention import (
    AUDIT_LOG_RETENTION_DAYS,
    MCP_AUDIT_LOG_RETENTION_DAYS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupResult:
    """Counts returned by a single cleanup pass.

    Surfaced both via the structured log line and via the admin
    endpoint's JSON response so ops can see "did anything actually
    get deleted on the last run?" without grepping logs.
    """

    audit_log_deleted: int
    mcp_audit_log_deleted: int

    @property
    def total(self) -> int:
        return self.audit_log_deleted + self.mcp_audit_log_deleted


async def cleanup_audit_logs(db: AsyncSession) -> int:
    """Delete ``audit_log_entries`` older than the retention window.

    Returns the number of rows actually removed (Postgres' DELETE
    returns ``rowcount`` reliably). The caller owns the surrounding
    transaction; we ``flush`` so the count is observable inside the
    same session but never commit — the orchestrator (cleanup loop
    or admin endpoint) decides when to commit.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=AUDIT_LOG_RETENTION_DAYS
    )
    stmt = delete(AuditLogEntry).where(AuditLogEntry.created_at < cutoff)
    result = await db.execute(stmt)
    await db.flush()
    deleted = int(result.rowcount or 0)
    return deleted


async def cleanup_mcp_audit_logs(db: AsyncSession) -> int:
    """Delete ``mcp_audit_log_entries`` older than the retention
    window. Same shape as :func:`cleanup_audit_logs`."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=MCP_AUDIT_LOG_RETENTION_DAYS
    )
    stmt = delete(McpAuditLogEntry).where(
        McpAuditLogEntry.created_at < cutoff
    )
    result = await db.execute(stmt)
    await db.flush()
    deleted = int(result.rowcount or 0)
    return deleted


async def run_all_cleanups(db: AsyncSession | None = None) -> CleanupResult:
    """Execute both cleanups in one transaction and log the result.

    If ``db`` is None we open our own session via
    ``async_session_factory`` — the daily background task uses
    that path so it doesn't depend on a request-scoped session.
    The admin endpoint passes its session in so the operation
    runs inside the same transaction as the auth checks.
    """
    own_session = db is None
    if db is None:
        session_cm = async_session_factory()
    else:
        # Reuse the caller's session; suppress the ``async with``
        # close behaviour by binding to a no-op context.
        session_cm = _PassthroughSession(db)

    async with session_cm as session:
        try:
            audit_deleted = await cleanup_audit_logs(session)
            mcp_deleted = await cleanup_mcp_audit_logs(session)
            if own_session:
                await session.commit()
        except Exception:
            if own_session:
                await session.rollback()
            logger.exception("dsgvo.cleanup.failed")
            raise

    result = CleanupResult(
        audit_log_deleted=audit_deleted,
        mcp_audit_log_deleted=mcp_deleted,
    )
    # Structured log line — single source of truth for "did the
    # nightly cleanup do anything". Grep target:
    # ``dsgvo.cleanup audit_log_deleted=``.
    logger.info(
        "dsgvo.cleanup audit_log_deleted=%d mcp_audit_log_deleted=%d total=%d",
        result.audit_log_deleted,
        result.mcp_audit_log_deleted,
        result.total,
    )
    return result


class _PassthroughSession:
    """Async-context-manager wrapper that does NOT close the
    underlying session on exit.

    Lets ``run_all_cleanups`` use ``async with`` uniformly whether
    we own the session (ours to close) or borrow it (caller's
    responsibility). Cleaner than branching on ``own_session``
    every time we use the session.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Deliberately no-op — caller closes.
        return None
