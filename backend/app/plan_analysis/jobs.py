"""Plananalyse als Hintergrund-Job im App-Prozess (2026-11).

Warum: die Analyse lief in der HTTP-Anfrage; Cloudflare bricht nach
~100 s ab (524), mehrseitige/gekachelte Pläne brauchen länger. Das
Backend rechnete dann zu Ende, der Nutzer sah aber nur einen Fehler.

Wie (bewusst ohne Redis/Queue/neuen Dienst):

* ``start_job`` legt eine ``plan_analysis_jobs``-Zeile an, setzt den
  Plan auf ``processing`` (beides sofort committet) und startet einen
  ``asyncio``-Task im laufenden Prozess. Die Route antwortet sofort.
* Der Task öffnet eine eigene DB-Session, ruft die unveränderte
  Pipeline (``analyze_plan`` / ``analyze_schnitt_plan``) auf und
  committet am Ende — dieselbe Transaktions-Semantik wie früher die
  Anfrage (Fehler → Rollback, keine halben Räume).
* Fortschritt (Seite x/y) und ein Heartbeat alle ``HEARTBEAT_S``
  Sekunden laufen über kurze, eigene Transaktionen auf der Job-Zeile.

Neustart während einer Analyse:

* Geordneter Shutdown (SIGTERM bei Deploy/Restart): ``shutdown()``
  bricht die Tasks ab, jeder markiert seinen Job als fehlgeschlagen
  ("durch einen Server-Neustart abgebrochen").
* Harter Abbruch (SIGKILL, OOM, Absturz): der Heartbeat bleibt stehen.
  ``fail_stale_jobs`` (beim Start und bei jeder Statusabfrage) setzt
  Jobs ohne Heartbeat seit ``STALE_AFTER_S`` auf fehlgeschlagen. Ein
  Job kann also nie ewig "läuft" anzeigen.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.plan import Plan
from app.db.models.plan_analysis_job import PlanAnalysisJob
from app.plan_analysis.pipeline import (
    PlanAnalysisError,
    analyze_plan,
    analyze_schnitt_plan,
)

logger = logging.getLogger(__name__)

HEARTBEAT_S = 15
STALE_AFTER_S = 120
# Obergrenze für einen ganzen Job. Einzelne KI-Calls haben eigene
# Timeouts (180 s); bei max. Seitenzahl × Kacheln bleibt das deutlich
# darunter — die Grenze fängt nur Hänger ab.
MAX_JOB_S = 30 * 60

MSG_RESTART = (
    "Die Analyse wurde durch einen Server-Neustart abgebrochen. "
    "Bitte starten Sie sie erneut."
)
MSG_STALE = (
    "Die Analyse wurde unterbrochen (Server nicht mehr erreichbar). "
    "Bitte starten Sie sie erneut."
)
MSG_TIMEOUT = (
    "Die Analyse hat zu lange gedauert und wurde abgebrochen. Bitte "
    "versuchen Sie es erneut oder teilen Sie den Plan in kleinere PDFs."
)
MSG_UNEXPECTED = (
    "Bei der KI-Analyse ist ein unerwarteter Fehler aufgetreten. "
    "Bitte versuchen Sie es erneut oder kontaktieren Sie den Support."
)

# Laufende Tasks — starke Referenzen, sonst kann der GC einen Task
# mitten im Lauf einsammeln (asyncio hält nur schwache Referenzen).
_TASKS: dict[UUID, asyncio.Task] = {}


def _session_factory() -> async_sessionmaker[AsyncSession]:
    # Late import + Funktion statt Modul-Konstante: Tests tauschen
    # ``app.plan_analysis.jobs.session_factory`` gegen ihre SQLite-
    # Factory, ohne dass beim Import schon eine Engine entsteht.
    global session_factory
    if session_factory is None:
        from app.db.session import async_session_factory

        session_factory = async_session_factory
    return session_factory


session_factory: async_sessionmaker[AsyncSession] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite (Tests) liefert naive Datumswerte zurück.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_stale(job: PlanAnalysisJob, now: datetime | None = None) -> bool:
    now = now or _now()
    hb = _aware(job.heartbeat_at)
    return job.status == "running" and (
        hb is None or now - hb > timedelta(seconds=STALE_AFTER_S)
    )


async def latest_job(db: AsyncSession, plan_id: UUID) -> PlanAnalysisJob | None:
    return (
        await db.execute(
            select(PlanAnalysisJob)
            .where(PlanAnalysisJob.plan_id == plan_id)
            .order_by(PlanAnalysisJob.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _mark_failed(
    db: AsyncSession, job: PlanAnalysisJob, detail: str, status: int
) -> None:
    job.status = "failed"
    job.error_detail = detail
    job.error_status = status
    job.finished_at = _now()
    # Plan-Zeile nur anfassen, wenn sie nicht gerade von einer noch
    # laufenden Analyse-Transaktion gesperrt ist (SKIP LOCKED) — sonst
    # würde eine Statusabfrage auf die Sperre warten.
    plan = (
        await db.execute(
            select(Plan)
            .where(Plan.id == job.plan_id, Plan.analysis_status == "processing")
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if plan is not None:
        plan.analysis_status = "failed"


async def fail_stale_jobs(db: AsyncSession, plan_id: UUID | None = None) -> int:
    """Jobs ohne Heartbeat seit ``STALE_AFTER_S`` → fehlgeschlagen.
    Committet nicht selbst."""
    cutoff = _now() - timedelta(seconds=STALE_AFTER_S)
    stmt = select(PlanAnalysisJob).where(
        PlanAnalysisJob.status == "running",
        PlanAnalysisJob.heartbeat_at < cutoff,
    )
    if plan_id is not None:
        stmt = stmt.where(PlanAnalysisJob.plan_id == plan_id)
    jobs = (await db.execute(stmt)).scalars().all()
    for job in jobs:
        if job.id in _TASKS and not _TASKS[job.id].done():
            # Läuft in DIESEM Prozess noch — nur der Heartbeat hing.
            continue
        logger.warning(
            "plan_job.stale job=%s plan=%s heartbeat=%s",
            job.id, job.plan_id, job.heartbeat_at,
        )
        await _mark_failed(db, job, MSG_STALE, 500)
    return len(jobs)


async def start_job(
    db: AsyncSession, plan: Plan, user_id: UUID, *, is_schnitt: bool
) -> PlanAnalysisJob:
    """Job anlegen + committen + Task starten. Wirft ``JobAlreadyRunning``,
    wenn für den Plan schon ein lebender Job läuft."""
    await fail_stale_jobs(db, plan.id)
    current = await latest_job(db, plan.id)
    if current is not None and current.status == "running":
        raise JobAlreadyRunning(current)

    now = _now()
    job = PlanAnalysisJob(
        plan_id=plan.id,
        user_id=user_id,
        status="running",
        pages_done=0,
        started_at=now,
        heartbeat_at=now,
    )
    db.add(job)
    plan.analysis_status = "processing"
    await db.commit()

    task = asyncio.create_task(
        _run_job(job.id, plan.id, user_id, is_schnitt),
        name=f"plan-analysis-{plan.id}",
    )
    _TASKS[job.id] = task
    task.add_done_callback(lambda _t, jid=job.id: _TASKS.pop(jid, None))
    logger.info("plan_job.started job=%s plan=%s schnitt=%s", job.id, plan.id, is_schnitt)
    return job


class JobAlreadyRunning(Exception):
    def __init__(self, job: PlanAnalysisJob):
        super().__init__("analysis already running")
        self.job = job


async def _update_job(job_id: UUID, **values: Any) -> None:
    async with _session_factory()() as s:
        await s.execute(
            update(PlanAnalysisJob)
            .where(PlanAnalysisJob.id == job_id, PlanAnalysisJob.status == "running")
            .values(**values)
        )
        await s.commit()


async def _heartbeat_loop(job_id: UUID) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        try:
            await _update_job(job_id, heartbeat_at=_now())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("plan_job.heartbeat_failed job=%s", job_id, exc_info=True)


async def _finish(
    job_id: UUID,
    *,
    status: str,
    result: dict | None = None,
    detail: str | None = None,
    http_status: int | None = None,
) -> None:
    """Endstatus in eigener Transaktion schreiben (nach Commit/Rollback
    der Analyse-Session — die Plan-Zeile ist dann frei)."""
    async with _session_factory()() as s:
        job = await s.get(PlanAnalysisJob, job_id)
        if job is None:  # Plan inzwischen gelöscht (CASCADE)
            return
        if status == "completed":
            job.status = "completed"
            job.result = result
            job.finished_at = _now()
            if result and result.get("pages_analyzed"):
                job.pages_done = job.pages_total or int(result["pages_analyzed"])
        else:
            await _mark_failed(s, job, detail or MSG_UNEXPECTED, http_status or 500)
        await s.commit()


async def _record_analytics(
    db: AsyncSession, user_id: UUID, result: dict, is_schnitt: bool
) -> None:
    # Wie früher in der Route — best effort, darf nie den Job kippen.
    try:
        from app.db.models.analytics import EVENT_PLAN_ANALYZED as _ANALYTICS_PLAN_ANALYZED
        from app.db.models.user import User
        from app.services import analytics as analytics_service

        user = await db.get(User, user_id)
        if user is None:
            return
        await analytics_service.record_event(
            db,
            event_type=_ANALYTICS_PLAN_ANALYZED,
            user=user,
            event_data={
                "pages": int(result.get("pages_analyzed", 0) or 0),
                "rooms_extracted": int(result.get("rooms_extracted", 0) or 0),
                "plan_type": "schnitt" if is_schnitt else "grundriss",
                "heights_matched": int(result.get("heights_matched", 0) or 0),
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("analytics.plan_analyzed failed — continuing")


async def run_pipeline(
    db: AsyncSession, plan_id: UUID, *, is_schnitt: bool, progress=None
) -> dict:
    """Dispatch Grundriss vs. Schnitt (früher direkt in der Route)."""
    if is_schnitt:
        return await analyze_schnitt_plan(plan_id, db, progress=progress)
    return await analyze_plan(plan_id, db, progress=progress)


async def _run_job(job_id: UUID, plan_id: UUID, user_id: UUID, is_schnitt: bool) -> None:
    hb = asyncio.create_task(_heartbeat_loop(job_id))

    async def progress(done: int, total: int) -> None:
        try:
            await _update_job(job_id, pages_done=done, pages_total=total, heartbeat_at=_now())
        except Exception:  # noqa: BLE001
            logger.warning("plan_job.progress_failed job=%s", job_id, exc_info=True)

    try:
        async with _session_factory()() as db:
            try:
                result = await asyncio.wait_for(
                    run_pipeline(db, plan_id, is_schnitt=is_schnitt, progress=progress),
                    timeout=MAX_JOB_S,
                )
                await _record_analytics(db, user_id, result, is_schnitt)
                await db.commit()
            except PlanAnalysisError as e:
                await db.rollback()
                await _finish(job_id, status="failed", detail=e.detail, http_status=422)
                logger.info("plan_job.failed job=%s plan=%s detail=%s", job_id, plan_id, e.detail)
                return
            except asyncio.TimeoutError:
                await db.rollback()
                await _finish(job_id, status="failed", detail=MSG_TIMEOUT, http_status=500)
                logger.error("plan_job.timeout job=%s plan=%s", job_id, plan_id)
                return
            except asyncio.CancelledError:
                # Shutdown/Neustart: Endstatus noch schreiben, dann
                # den Abbruch weiterreichen. ``shield`` schützt den
                # Schreibvorgang vor einem zweiten Cancel.
                await asyncio.shield(_rollback_and_fail(db, job_id))
                logger.warning("plan_job.cancelled job=%s plan=%s", job_id, plan_id)
                raise
            except Exception:  # noqa: BLE001
                logger.exception("plan_job.unexpected job=%s plan=%s", job_id, plan_id)
                await db.rollback()
                await _finish(job_id, status="failed", detail=MSG_UNEXPECTED, http_status=500)
                return
        await _finish(job_id, status="completed", result=result)
        logger.info("plan_job.completed job=%s plan=%s", job_id, plan_id)
    finally:
        hb.cancel()
        # Den Abbruch abwarten: sonst überlebt ein halb laufender
        # Heartbeat-Schreibvorgang das Jobende (Tests: sporadischer
        # Hänger beim Abbau der Event-Loop, ~1 von 4 Läufen).
        await asyncio.gather(hb, return_exceptions=True)


async def _rollback_and_fail(db: AsyncSession, job_id: UUID) -> None:
    try:
        await db.rollback()
    except Exception:  # noqa: BLE001
        pass
    try:
        await _finish(job_id, status="failed", detail=MSG_RESTART, http_status=500)
    except Exception:  # noqa: BLE001
        logger.exception("plan_job.cancel_mark_failed_failed job=%s", job_id)


async def shutdown(timeout: float = 10.0) -> None:
    """Lifespan-Ende: laufende Analysen abbrechen; jede schreibt dabei
    ihren Job auf "fehlgeschlagen" (siehe ``_run_job``)."""
    tasks = [t for t in _TASKS.values() if not t.done()]
    if not tasks:
        return
    logger.warning("plan_job.shutdown cancelling=%d", len(tasks))
    for t in tasks:
        t.cancel()
    await asyncio.wait(tasks, timeout=timeout)


async def startup_sweep() -> None:
    """Beim Start: Jobs, deren Prozess hart gestorben ist, beenden."""
    try:
        async with _session_factory()() as db:
            n = await fail_stale_jobs(db)
            await db.commit()
        if n:
            logger.warning("plan_job.startup_sweep failed_stale=%d", n)
    except Exception:  # noqa: BLE001
        logger.exception("plan_job.startup_sweep_failed")
