"""Tests for the v24.1 + v24.2 Plan-Typ workflow.

Coverage:

  1. Upload accepts each of the three valid plan_type values
     (grundriss, schnitt, lageplan) and persists them.
  2. Upload rejects an unknown plan_type with a 400 + German
     message.
  3. Analyse-endpoint accepts grundriss (+ legacy NULL plan_type
     as back-compat).
  4. Analyse-endpoint refuses lageplan with a 400 + German message.
  5. **v24.2** — Analyse-endpoint now ROUTES schnitt plans to
     ``analyze_schnitt_plan`` (height-extraction pipeline). The
     pre-v24.2 placeholder 400 ("in Vorbereitung") is gone.

The upload tests reuse the helper pattern from
``test_plans_upload_metadata.py`` (synthetic PDF via fitz, mock
upload_path) so they don't write into the dev upload dir.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.plans import (
    ALLOWED_PLAN_TYPES,
    PLAN_TYPE_GRUNDRISS,
    PLAN_TYPE_LAGEPLAN,
    PLAN_TYPE_SCHNITT,
    trigger_analysis,
    upload_plan,
)
from app.db.models.plan import Plan
from app.db.models.project import Project
from app.db.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_project(
    db: AsyncSession,
) -> tuple[User, Project]:
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db.add(project)
    await db.commit()
    return user, project


def _make_pdf_bytes() -> bytes:
    """Tiny valid PDF — a single empty page is enough for the upload
    path; we don't actually run the analyse pipeline against this."""
    import fitz

    doc = fitz.open()
    doc.new_page()
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_upload_file(filename: str, data: bytes) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(data),
        headers={"content-type": "application/pdf"},
    )


# Echte Datei: ``trigger_analysis`` prüft seit 2026-11, ob die Plan-
# Datei noch existiert (``Plan.file_available``).
import tempfile as _tempfile
_REAL_PDF = _tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name


async def _seed_plan_with_type(
    db: AsyncSession,
    *,
    user: User,
    project: Project,
    plan_type: str | None,
) -> Plan:
    plan = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="x.pdf",
        file_path=_REAL_PDF,
        plan_type=plan_type,
    )
    db.add(plan)
    await db.commit()
    return plan


# ---------------------------------------------------------------------------
# 1. Upload accepts each valid plan_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan_type",
    [PLAN_TYPE_GRUNDRISS, PLAN_TYPE_SCHNITT, PLAN_TYPE_LAGEPLAN],
)
@pytest.mark.asyncio
async def test_upload_persists_each_valid_plan_type(
    db_session: AsyncSession, tmp_path, plan_type
):
    user, project = await _seed_user_and_project(db_session)
    pdf = _make_pdf_bytes()
    upload = _make_upload_file(f"{plan_type}.pdf", pdf)

    with patch("app.api.plans.settings") as mock_settings:
        mock_settings.upload_path = Path(tmp_path)
        mock_settings.max_plan_file_mb = 25

        plan = await upload_plan(
            project_id=project.id,
            file=upload,
            plan_type=plan_type,
            user=user,
            db=db_session,
        )
        await db_session.commit()

    assert plan.plan_type == plan_type
    # Round-trip via DB to confirm persistence (not just an in-memory ORM hit).
    fetched = await db_session.get(Plan, plan.id)
    assert fetched is not None and fetched.plan_type == plan_type


# ---------------------------------------------------------------------------
# 2. Upload rejects an unknown plan_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_rejects_unknown_plan_type(
    db_session: AsyncSession, tmp_path
):
    user, project = await _seed_user_and_project(db_session)
    pdf = _make_pdf_bytes()
    upload = _make_upload_file("x.pdf", pdf)

    with patch("app.api.plans.settings") as mock_settings:
        mock_settings.upload_path = Path(tmp_path)
        mock_settings.max_plan_file_mb = 25

        with pytest.raises(HTTPException) as exc_info:
            await upload_plan(
                project_id=project.id,
                file=upload,
                plan_type="balconyplan",  # not on the whitelist
                user=user,
                db=db_session,
            )

    assert exc_info.value.status_code == 400
    assert "Plan-Typ" in exc_info.value.detail
    # The whitelist guard runs BEFORE the file is touched on disk
    # — confirm no Plan row landed in the DB and no file appeared.
    files_in_tmp = list(Path(tmp_path).rglob("*.pdf"))
    assert files_in_tmp == []


# ---------------------------------------------------------------------------
# 3. Analyse accepts grundriss + legacy NULL plan_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plan_type", [PLAN_TYPE_GRUNDRISS, None])
@pytest.mark.asyncio
async def test_analyse_accepts_grundriss_and_legacy_null(
    db_session: AsyncSession, plan_type
):
    """Grundriss plans + pre-v24.1 plans with NULL plan_type both
    fall through the v24.1 type-gate and start a Grundriss job
    (2026-11: Hintergrund-Job, Route antwortet 202)."""
    user, project = await _seed_user_and_project(db_session)
    plan = await _seed_plan_with_type(
        db_session, user=user, project=project, plan_type=plan_type
    )

    fake_job = _fake_job(plan)
    response = Response()
    with patch(
        "app.api.plans.plan_jobs.start_job", new=AsyncMock(return_value=fake_job)
    ) as start, patch("app.api.plans.verify_plan_owner", return_value=plan):
        result = await trigger_analysis(
            plan_id=plan.id, response=response, user=user, db=db_session
        )

    start.assert_awaited_once()
    assert start.await_args.kwargs["is_schnitt"] is False
    assert response.status_code == 202
    assert result["plan_id"] == str(plan.id)
    assert result["status"] == "running"


# ---------------------------------------------------------------------------
# 4. Analyse refuses lageplan with 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_refuses_lageplan(db_session: AsyncSession):
    user, project = await _seed_user_and_project(db_session)
    plan = await _seed_plan_with_type(
        db_session, user=user, project=project, plan_type=PLAN_TYPE_LAGEPLAN
    )

    # Kein Job darf starten, wenn das Typ-Gate greift.
    with patch(
        "app.api.plans.plan_jobs.start_job", new=AsyncMock()
    ) as start, patch("app.api.plans.verify_plan_owner", return_value=plan):
        with pytest.raises(HTTPException) as exc_info:
            await trigger_analysis(
                plan_id=plan.id, response=Response(), user=user, db=db_session
            )
        start.assert_not_awaited()

    assert exc_info.value.status_code == 400
    assert "Lagepläne" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 5. Analyse routes schnitt to analyze_schnitt_plan (v24.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_routes_schnitt_to_schnitt_pipeline(
    db_session: AsyncSession,
):
    """v24.2 — Schnitt plans dispatch to ``analyze_schnitt_plan``;
    the Grundriss ``analyze_plan`` must NOT run for a Schnitt (wrong
    prompt, wasted Vision budget). 2026-11: Route startet den Job mit
    ``is_schnitt=True``; der Job-Dispatcher wählt die Pipeline."""
    user, project = await _seed_user_and_project(db_session)
    plan = await _seed_plan_with_type(
        db_session, user=user, project=project, plan_type=PLAN_TYPE_SCHNITT
    )

    with patch(
        "app.api.plans.plan_jobs.start_job", new=AsyncMock(return_value=_fake_job(plan))
    ) as start, patch("app.api.plans.verify_plan_owner", return_value=plan):
        await trigger_analysis(
            plan_id=plan.id, response=Response(), user=user, db=db_session
        )
    assert start.await_args.kwargs["is_schnitt"] is True

    from app.plan_analysis import jobs

    with patch.object(jobs, "analyze_plan", new=AsyncMock()) as grundriss_spy, patch.object(
        jobs, "analyze_schnitt_plan",
        new=AsyncMock(return_value={"plan_id": str(plan.id), "heights_matched": 2}),
    ) as schnitt_spy:
        result = await jobs.run_pipeline(db_session, plan.id, is_schnitt=True)

    grundriss_spy.assert_not_awaited()
    schnitt_spy.assert_awaited_once()
    assert result["heights_matched"] == 2


def _fake_job(plan):
    from datetime import datetime, timezone

    from app.db.models.plan_analysis_job import PlanAnalysisJob

    now = datetime.now(timezone.utc)
    return PlanAnalysisJob(
        id=uuid.uuid4(), plan_id=plan.id, status="running", pages_done=0,
        started_at=now, heartbeat_at=now,
    )


# ---------------------------------------------------------------------------
# Defence: Whitelist constant is consistent across files
# ---------------------------------------------------------------------------


def test_allowed_plan_types_constant_is_complete():
    """Lock the canonical whitelist size — anyone adding a new
    plan_type needs to update this test + the frontend's
    PLAN_TYPE_LABELS in tandem."""
    assert ALLOWED_PLAN_TYPES == {
        PLAN_TYPE_GRUNDRISS,
        PLAN_TYPE_SCHNITT,
        PLAN_TYPE_LAGEPLAN,
    }
