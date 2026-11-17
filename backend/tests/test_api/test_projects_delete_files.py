"""Bug-Fix: Projekt löschen entfernt auch die Plan-Dateien (DSGVO Art. 17).

Vorher: ``DELETE /api/projects/{id}`` löschte nur die DB-Zeilen (Cascade);
die PDFs unter ``upload_path/<project_id>/`` blieben auf der Platte.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import delete_project
from app.config import settings
from app.db.models.plan import Plan
from app.db.models.project import Project
from app.db.models.user import User


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    return settings.upload_path


async def _seed(db: AsyncSession, upload_root, name: str):
    user = User(
        id=uuid.uuid4(),
        email=f"{name}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name=name,
    )
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name=name)
    db.add(project)
    await db.flush()
    pdir = upload_root / str(project.id)
    pdir.mkdir(parents=True)
    pdf = pdir / "abc_plan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    db.add(
        Plan(
            id=uuid.uuid4(),
            project_id=project.id,
            filename="plan.pdf",
            file_path=str(pdf),
            file_size_bytes=8,
        )
    )
    await db.commit()
    return user, project, pdir


@pytest.mark.asyncio
async def test_delete_project_removes_plan_files(db_session: AsyncSession, upload_root):
    user, project, pdir = await _seed(db_session, upload_root, "owner")
    other_user, other_project, other_dir = await _seed(db_session, upload_root, "other")

    await delete_project(project.id, user=user, db=db_session)
    await db_session.commit()

    assert not pdir.exists()
    assert await db_session.get(Project, project.id) is None
    # Fremdes Projekt bleibt unberührt.
    assert other_dir.exists() and any(other_dir.iterdir())


@pytest.mark.asyncio
async def test_delete_foreign_project_keeps_files(db_session: AsyncSession, upload_root):
    user, project, pdir = await _seed(db_session, upload_root, "owner")
    intruder, _, _ = await _seed(db_session, upload_root, "intruder")

    with pytest.raises(HTTPException) as exc:
        await delete_project(project.id, user=intruder, db=db_session)
    assert exc.value.status_code == 403
    assert pdir.exists() and any(pdir.iterdir())


@pytest.mark.asyncio
async def test_delete_project_without_files_is_fine(db_session: AsyncSession, upload_root):
    user = User(id=uuid.uuid4(), email="nofiles@example.com", password_hash="x", full_name="n")
    db_session.add(user)
    await db_session.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="leer")
    db_session.add(project)
    await db_session.commit()

    await delete_project(project.id, user=user, db=db_session)
    await db_session.commit()
    assert await db_session.get(Project, project.id) is None
