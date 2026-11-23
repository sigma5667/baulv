"""2026-11 — Plananalyse als Hintergrund-Job (``app.plan_analysis.jobs``).

Geprüft wird das Lebenszyklus-Verhalten, nicht die KI: die Pipeline ist
durch eine Attrappe ersetzt. Entscheidend sind die Endzustände — vor
allem, dass ein Job nach Server-Neustart oder hartem Absturz NIE ewig
auf "running" stehen bleibt.

SQLite als Datei (nicht :memory:), weil der Job mit eigenen Sessions
arbeitet und alle Sessions dieselbe DB sehen müssen.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.plan import Plan
from app.db.models.plan_analysis_job import PlanAnalysisJob
from app.db.models.project import Project
from app.db.models.user import User
from app.plan_analysis import jobs
from app.plan_analysis.pipeline import PlanAnalysisError


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    with patch.object(jobs, "session_factory", f):
        yield f
    await jobs.shutdown(timeout=2)
    await engine.dispose()


async def _seed(f) -> tuple[uuid.UUID, uuid.UUID]:
    async with f() as s:
        user = User(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com",
                    password_hash="x", full_name="T")
        s.add(user)
        await s.flush()
        project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
        s.add(project)
        await s.flush()
        plan = Plan(id=uuid.uuid4(), project_id=project.id, filename="x.pdf",
                    file_path="/tmp/x.pdf", plan_type="grundriss")
        s.add(plan)
        await s.commit()
        return plan.id, user.id


async def _start(f, plan_id, user_id, *, is_schnitt=False):
    async with f() as s:
        plan = await s.get(Plan, plan_id)
        return await jobs.start_job(s, plan, user_id, is_schnitt=is_schnitt)


async def _job(f, job_id) -> PlanAnalysisJob:
    async with f() as s:
        return await s.get(PlanAnalysisJob, job_id)


async def _plan_status(f, plan_id) -> str:
    async with f() as s:
        return (await s.get(Plan, plan_id)).analysis_status


async def _wait_done(f, job_id, timeout=5.0) -> PlanAnalysisJob:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        job = await _job(f, job_id)
        if job.status != "running":
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("Job wurde nicht fertig")


def _fake_pipeline(*, pages=2, delay=0.0, fail: Exception | None = None):
    calls = []

    async def fake(plan_id, db, progress=None):
        for i in range(pages):
            await progress(i, pages)
            calls.append(i)
            await asyncio.sleep(delay)
        if fail is not None:
            raise fail
        plan = await db.get(Plan, plan_id)
        plan.analysis_status = "completed"
        return {"plan_id": str(plan_id), "pages_analyzed": pages,
                "rooms_extracted": 7, "page_errors": []}

    fake.calls = calls
    return fake


@pytest.mark.asyncio
async def test_job_completes_and_stores_result(factory):
    plan_id, user_id = await _seed(factory)
    with patch.object(jobs, "analyze_plan", _fake_pipeline(pages=3)):
        job = await _start(factory, plan_id, user_id)
        assert job.status == "running"
        assert await _plan_status(factory, plan_id) == "processing"
        done = await _wait_done(factory, job.id)

    assert done.status == "completed"
    assert done.result["rooms_extracted"] == 7
    assert done.pages_total == 3 and done.pages_done == 3
    assert done.finished_at is not None
    assert await _plan_status(factory, plan_id) == "completed"


@pytest.mark.asyncio
async def test_pipeline_error_marks_job_and_plan_failed(factory):
    plan_id, user_id = await _seed(factory)
    err = PlanAnalysisError("Die KI konnte auf diesem Plan keine Räume erkennen.")
    with patch.object(jobs, "analyze_plan", _fake_pipeline(fail=err)):
        job = await _start(factory, plan_id, user_id)
        done = await _wait_done(factory, job.id)

    assert done.status == "failed"
    assert done.error_status == 422
    assert "keine Räume" in done.error_detail
    assert await _plan_status(factory, plan_id) == "failed"


@pytest.mark.asyncio
async def test_unexpected_error_is_generic_500(factory):
    plan_id, user_id = await _seed(factory)
    with patch.object(jobs, "analyze_plan", _fake_pipeline(fail=RuntimeError("boom"))):
        job = await _start(factory, plan_id, user_id)
        done = await _wait_done(factory, job.id)
    assert done.status == "failed" and done.error_status == 500
    assert "boom" not in done.error_detail


@pytest.mark.asyncio
async def test_second_start_while_running_is_rejected(factory):
    plan_id, user_id = await _seed(factory)
    with patch.object(jobs, "analyze_plan", _fake_pipeline(pages=1, delay=0.5)):
        job = await _start(factory, plan_id, user_id)
        with pytest.raises(jobs.JobAlreadyRunning) as exc:
            await _start(factory, plan_id, user_id)
        assert exc.value.job.id == job.id
        await _wait_done(factory, job.id)
        # Nach dem Ende darf neu gestartet werden.
        again = await _start(factory, plan_id, user_id)
        await _wait_done(factory, again.id)


@pytest.mark.asyncio
async def test_shutdown_during_analysis_marks_failed_not_running(factory):
    """Geordneter Neustart (SIGTERM → Lifespan-Ende → ``shutdown``)."""
    plan_id, user_id = await _seed(factory)
    with patch.object(jobs, "analyze_plan", _fake_pipeline(pages=1, delay=30)):
        job = await _start(factory, plan_id, user_id)
        await asyncio.sleep(0.1)
        await jobs.shutdown(timeout=5)

    done = await _job(factory, job.id)
    assert done.status == "failed"
    assert done.error_detail == jobs.MSG_RESTART
    assert await _plan_status(factory, plan_id) == "failed"


@pytest.mark.asyncio
async def test_hard_crash_is_detected_via_stale_heartbeat(factory):
    """Harter Absturz: kein Task mehr, Heartbeat steht → nach
    ``STALE_AFTER_S`` fehlgeschlagen (Startup-Sweep + Statusabfrage)."""
    plan_id, user_id = await _seed(factory)
    old = datetime.now(timezone.utc) - timedelta(seconds=jobs.STALE_AFTER_S + 5)
    fresh = datetime.now(timezone.utc)
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        plan.analysis_status = "processing"
        dead = PlanAnalysisJob(plan_id=plan_id, user_id=user_id, status="running",
                               pages_done=1, pages_total=4, started_at=old, heartbeat_at=old)
        s.add(dead)
        await s.commit()

    # Frischer Heartbeat zählt NICHT als tot.
    async with factory() as s:
        j = await s.get(PlanAnalysisJob, dead.id)
        j.heartbeat_at = fresh
        await s.commit()
    await jobs.startup_sweep()
    assert (await _job(factory, dead.id)).status == "running"

    async with factory() as s:
        j = await s.get(PlanAnalysisJob, dead.id)
        j.heartbeat_at = old
        await s.commit()
    await jobs.startup_sweep()
    done = await _job(factory, dead.id)
    assert done.status == "failed"
    assert done.error_detail == jobs.MSG_STALE
    assert await _plan_status(factory, plan_id) == "failed"


@pytest.mark.asyncio
async def test_heartbeat_advances_during_long_page(factory):
    plan_id, user_id = await _seed(factory)
    with patch.object(jobs, "HEARTBEAT_S", 0.05), patch.object(
        jobs, "analyze_plan", _fake_pipeline(pages=1, delay=0.4)
    ):
        job = await _start(factory, plan_id, user_id)
        first = (await _job(factory, job.id)).heartbeat_at
        await asyncio.sleep(0.25)
        later = (await _job(factory, job.id)).heartbeat_at
        await _wait_done(factory, job.id)
    assert later > first


@pytest.mark.asyncio
async def test_status_route_reports_progress_and_stale(factory):
    from app.api.plans import get_analysis_status

    plan_id, user_id = await _seed(factory)
    old = datetime.now(timezone.utc) - timedelta(seconds=jobs.STALE_AFTER_S + 5)
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        plan.analysis_status = "processing"
        s.add(PlanAnalysisJob(plan_id=plan_id, user_id=user_id, status="running",
                              pages_done=2, pages_total=5, started_at=old, heartbeat_at=old))
        await s.commit()

    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        with patch("app.api.plans.verify_plan_owner", return_value=plan):
            body = await get_analysis_status(plan_id=plan_id, user=None, db=s)
        await s.commit()
    assert body["status"] == "failed"
    assert body["pages_done"] == 2 and body["pages_total"] == 5
    assert body["error_detail"] == jobs.MSG_STALE


@pytest.mark.asyncio
async def test_status_route_404_without_job_and_legacy_processing(factory):
    from app.api.plans import get_analysis_status

    plan_id, _ = await _seed(factory)
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        with patch("app.api.plans.verify_plan_owner", return_value=plan):
            with pytest.raises(HTTPException) as exc:
                await get_analysis_status(plan_id=plan_id, user=None, db=s)
    assert exc.value.status_code == 404

    # Altbestand: "processing" ohne Job (synchrone Anfrage von früher
    # ist abgebrochen) → fehlgeschlagen statt ewig "läuft".
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        plan.analysis_status = "processing"
        await s.commit()
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        with patch("app.api.plans.verify_plan_owner", return_value=plan):
            body = await get_analysis_status(plan_id=plan_id, user=None, db=s)
        await s.commit()
    assert body["status"] == "failed"
    assert await _plan_status(factory, plan_id) == "failed"
