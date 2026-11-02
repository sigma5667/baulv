"""Atomare Tageszähler (Audit 2026-11) — siehe ``DailyUsageCounter``."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.usage_counter import DailyUsageCounter


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def increment(db: AsyncSession, key: str) -> int:
    """Zähler für heute (UTC) um 1 erhöhen und den neuen Stand liefern.

    Ein einziges ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` — damit
    zählen parallele Requests nie doppelt oder zu wenig.
    """
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # SQLite in den Tests
        from sqlalchemy.dialects.sqlite import insert

    stmt = insert(DailyUsageCounter).values(counter_key=key, day=_today(), count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["counter_key", "day"],
        set_={"count": DailyUsageCounter.count + 1},
    ).returning(DailyUsageCounter.count)
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def purge_older_than(db: AsyncSession, days: int = 7) -> int:
    """Zählerstände älter als ``days`` Tage löschen (Nightly-Cleanup)."""
    cutoff = _today() - timedelta(days=days)
    result = await db.execute(
        delete(DailyUsageCounter).where(DailyUsageCounter.day < cutoff)
    )
    return result.rowcount or 0
