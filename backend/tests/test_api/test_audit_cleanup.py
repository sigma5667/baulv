"""Tests for the v23.3 DSGVO Art. 5(1)(e) audit-log retention.

Coverage matches the DS-2 spec:

  1. Old audit-log entries (> 730 days) are deleted.
  2. Young entries (< 730 days) survive — the cutoff is a
     half-open interval, so an entry born exactly 729 days ago
     stays.
  3. Cleanup runs cleanly against an empty database without
     raising or producing a misleading row count.
  4. The structured ``dsgvo.cleanup`` log line lands with both
     count fields populated.

Test data is seeded directly via the model classes — we don't
exercise the FastAPI endpoint here because the endpoint's auth
gate is independently tested via the ADMIN_EMAILS allow-list logic;
the cleanup *function* is the surface that matters for compliance.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditLogEntry
from app.db.models.mcp_audit import McpAuditLogEntry, RESULT_OK
from app.dsgvo_retention import (
    AUDIT_LOG_RETENTION_DAYS,
    MCP_AUDIT_LOG_RETENTION_DAYS,
)
from app.services.audit_cleanup import (
    cleanup_audit_logs,
    cleanup_mcp_audit_logs,
    run_all_cleanups,
)


# ---------------------------------------------------------------------------
# Retention constants are pinned — bumping them is a deliberate two-step
# (constant + frontend hint copy)
# ---------------------------------------------------------------------------


def test_retention_constants_match_24_months():
    """24 months = 730 days. If a future spec change moves to 36
    months, the SPA's "Audit-Einträge werden nach 24 Monaten gelöscht"
    hint also needs to update — the test pin makes that obvious."""
    assert AUDIT_LOG_RETENTION_DAYS == 730
    assert MCP_AUDIT_LOG_RETENTION_DAYS == 730


# ---------------------------------------------------------------------------
# 1. Old entries are deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_deletes_audit_log_entries_older_than_730_days(
    db_session: AsyncSession,
):
    """A row whose ``created_at`` is 800 days ago is past the
    retention window and must be removed."""
    old_entry = AuditLogEntry(
        id=uuid.uuid4(),
        user_id=None,
        event_type="user.login",
        created_at=datetime.now(timezone.utc) - timedelta(days=800),
    )
    db_session.add(old_entry)
    await db_session.commit()

    deleted = await cleanup_audit_logs(db_session)
    await db_session.commit()

    assert deleted == 1
    rows = (
        await db_session.execute(select(AuditLogEntry))
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_cleanup_deletes_mcp_audit_entries_older_than_730_days(
    db_session: AsyncSession,
):
    """Mirror of the audit-log test for the MCP table — same
    retention window, same expected behaviour."""
    old_mcp = McpAuditLogEntry(
        id=uuid.uuid4(),
        user_id=None,
        api_key_id=None,
        tool_name="list_projects",
        result=RESULT_OK,
        created_at=datetime.now(timezone.utc) - timedelta(days=900),
    )
    db_session.add(old_mcp)
    await db_session.commit()

    deleted = await cleanup_mcp_audit_logs(db_session)
    await db_session.commit()

    assert deleted == 1


# ---------------------------------------------------------------------------
# 2. Young entries are preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_preserves_young_entries(
    db_session: AsyncSession,
):
    """Entries inside the 730-day window must survive the cleanup.
    We seed two boundary cases — 1 day old and 729 days old — to
    guard against an off-by-one in the cutoff comparison."""
    one_day = AuditLogEntry(
        id=uuid.uuid4(),
        user_id=None,
        event_type="user.login",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    seven_twenty_nine = AuditLogEntry(
        id=uuid.uuid4(),
        user_id=None,
        event_type="user.login",
        created_at=datetime.now(timezone.utc) - timedelta(days=729),
    )
    db_session.add_all([one_day, seven_twenty_nine])
    await db_session.commit()

    deleted = await cleanup_audit_logs(db_session)
    await db_session.commit()

    assert deleted == 0
    rows = (
        await db_session.execute(select(AuditLogEntry))
    ).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_cleanup_mixed_old_and_young(db_session: AsyncSession):
    """Real-world case: a few of each age. Cleanup deletes only the
    old subset, leaves the young ones, returns the precise count."""
    now = datetime.now(timezone.utc)
    old_one = AuditLogEntry(
        id=uuid.uuid4(),
        user_id=None,
        event_type="user.login",
        created_at=now - timedelta(days=800),
    )
    old_two = AuditLogEntry(
        id=uuid.uuid4(),
        user_id=None,
        event_type="user.password_changed",
        created_at=now - timedelta(days=731),
    )
    young = AuditLogEntry(
        id=uuid.uuid4(),
        user_id=None,
        event_type="user.login",
        created_at=now - timedelta(days=30),
    )
    db_session.add_all([old_one, old_two, young])
    await db_session.commit()

    deleted = await cleanup_audit_logs(db_session)
    await db_session.commit()

    assert deleted == 2
    surviving = (
        await db_session.execute(select(AuditLogEntry))
    ).scalars().all()
    assert len(surviving) == 1
    assert surviving[0].id == young.id


# ---------------------------------------------------------------------------
# 3. Empty DB doesn't crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_empty_database_returns_zero(
    db_session: AsyncSession,
):
    """A freshly-deployed instance has no audit rows yet. The
    cleanup must return 0 / 0 cleanly, not error out, and not log
    a confusing "deleted N rows" line. ``run_all_cleanups`` is the
    high-level entry point; both sub-functions exercised together
    via that single call."""
    result = await run_all_cleanups(db_session)

    assert result.audit_log_deleted == 0
    assert result.mcp_audit_log_deleted == 0
    assert result.total == 0


# ---------------------------------------------------------------------------
# 4. Structured logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_emits_structured_log_line(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
):
    """The single grep-target the operator relies on is the
    ``dsgvo.cleanup audit_log_deleted=X mcp_audit_log_deleted=Y
    total=Z`` info line. Pin its presence and the field names so a
    refactor can't quietly drop them."""
    db_session.add(
        AuditLogEntry(
            id=uuid.uuid4(),
            user_id=None,
            event_type="user.login",
            created_at=datetime.now(timezone.utc) - timedelta(days=800),
        )
    )
    await db_session.commit()

    with caplog.at_level(logging.INFO, logger="app.services.audit_cleanup"):
        result = await run_all_cleanups(db_session)

    assert result.audit_log_deleted == 1

    matching = [
        rec for rec in caplog.records if "dsgvo.cleanup" in rec.getMessage()
    ]
    assert matching, "Expected a 'dsgvo.cleanup' log line"
    msg = matching[0].getMessage()
    # All three count fields present, with the right names.
    assert "audit_log_deleted=1" in msg
    assert "mcp_audit_log_deleted=" in msg
    assert "total=" in msg
