"""Pläne ohne Datei werden gekennzeichnet, nicht gelöscht.

Hintergrund: bis 2026-11 lief die App ohne dauerhaften Speicher;
Plan-PDFs gingen bei jedem Redeploy verloren. Die DB-Zeilen (und die
daraus extrahierten Räume) bleiben — die API meldet
``file_available=False`` und lehnt eine neue Analyse mit einer klaren
Meldung ab (409) statt tief in der Pipeline zu scheitern.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.plans import trigger_analysis
from app.config import settings
from app.db.models.plan import Plan
from app.db.models.project import Project
from app.db.models.user import User
from app.schemas.plan import PlanResponse


async def _seed(db: AsyncSession, tmp_path, *, with_file: bool):
    user = User(
        id=uuid.uuid4(),
        email=f"fm-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="FM",
        subscription_plan="pro",
    )
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db.add(project)
    await db.flush()
    path = tmp_path / f"{uuid.uuid4()}.pdf"
    if with_file:
        path.write_bytes(b"%PDF-1.4")
    plan = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="plan.pdf",
        file_path=str(path),
        file_size_bytes=8,
        plan_type="grundriss",
    )
    db.add(plan)
    await db.commit()
    return user, plan


@pytest.mark.asyncio
async def test_missing_file_is_flagged_not_deleted(db_session: AsyncSession, tmp_path):
    _, plan = await _seed(db_session, tmp_path, with_file=False)

    assert plan.file_available is False
    assert PlanResponse.model_validate(plan).file_available is False
    # Zeile bleibt bestehen.
    assert await db_session.get(Plan, plan.id) is not None


@pytest.mark.asyncio
async def test_present_file_is_available(db_session: AsyncSession, tmp_path):
    _, plan = await _seed(db_session, tmp_path, with_file=True)

    assert plan.file_available is True
    assert PlanResponse.model_validate(plan).file_available is True


@pytest.mark.asyncio
async def test_analysis_of_missing_file_is_clear_409(
    db_session: AsyncSession, tmp_path, monkeypatch
):
    user, plan = await _seed(db_session, tmp_path, with_file=False)
    monkeypatch.setattr(settings, "beta_unlock_all_features", True)

    with pytest.raises(HTTPException) as exc:
        await trigger_analysis(plan.id, response=Response(), user=user, db=db_session)
    assert exc.value.status_code == 409
    assert "nicht mehr vorhanden" in exc.value.detail
    # Status unverändert, nichts gelöscht.
    await db_session.refresh(plan)
    assert plan.analysis_status == "pending"
