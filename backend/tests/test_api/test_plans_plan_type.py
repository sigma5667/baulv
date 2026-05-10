"""Tests for the v24.1 Plan-Typ workflow.

Coverage:

  1. Upload accepts each of the three valid plan_type values
     (grundriss, schnitt, lageplan) and persists them.
  2. Upload rejects an unknown plan_type with a 400 + German
     message.
  3. Analyse-endpoint accepts grundriss (+ legacy NULL plan_type
     as back-compat).
  4. Analyse-endpoint refuses lageplan with a 400 + German message.
  5. Analyse-endpoint refuses schnitt with a 400 + "in Vorbereitung"
     message — placeholder until v24.2 ships height extraction.

The upload tests reuse the helper pattern from
``test_plans_upload_metadata.py`` (synthetic PDF via fitz, mock
upload_path) so they don't write into the dev upload dir.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException, UploadFile
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
        file_path="/tmp/x.pdf",
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
    fall through the v24.1 type-gate without triggering the
    Lageplan/Schnitt rejection branches."""
    user, project = await _seed_user_and_project(db_session)
    plan = await _seed_plan_with_type(
        db_session, user=user, project=project, plan_type=plan_type
    )

    # We mock the heavy ``analyze_plan`` pipeline call; the test
    # only cares that the v24.1 type-gate let the request through.
    with patch("app.api.plans.analyze_plan") as mock_analyze, patch(
        "app.api.plans.verify_plan_owner",
        return_value=plan,
    ):
        mock_analyze.return_value = {
            "plan_id": str(plan.id),
            "pages_analyzed": 0,
            "rooms_extracted": 0,
            "page_errors": [],
        }
        result = await trigger_analysis(
            plan_id=plan.id, user=user, db=db_session
        )

    mock_analyze.assert_called_once()
    assert result["plan_id"] == str(plan.id)


# ---------------------------------------------------------------------------
# 4. Analyse refuses lageplan with 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_refuses_lageplan(db_session: AsyncSession):
    user, project = await _seed_user_and_project(db_session)
    plan = await _seed_plan_with_type(
        db_session, user=user, project=project, plan_type=PLAN_TYPE_LAGEPLAN
    )

    # Engine must NOT be invoked when the type-gate trips.
    with patch("app.api.plans.analyze_plan") as engine_spy, patch(
        "app.api.plans.verify_plan_owner",
        return_value=plan,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await trigger_analysis(
                plan_id=plan.id, user=user, db=db_session
            )
        engine_spy.assert_not_called()

    assert exc_info.value.status_code == 400
    assert "Lagepläne" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 5. Analyse refuses schnitt with 400 + "in Vorbereitung"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_refuses_schnitt_with_v24_2_hint(
    db_session: AsyncSession,
):
    user, project = await _seed_user_and_project(db_session)
    plan = await _seed_plan_with_type(
        db_session, user=user, project=project, plan_type=PLAN_TYPE_SCHNITT
    )

    with patch("app.api.plans.analyze_plan") as engine_spy, patch(
        "app.api.plans.verify_plan_owner",
        return_value=plan,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await trigger_analysis(
                plan_id=plan.id, user=user, db=db_session
            )
        engine_spy.assert_not_called()

    assert exc_info.value.status_code == 400
    # Pin the German wording so the frontend's special-casing of
    # "in Vorbereitung" doesn't drift away from this string.
    assert "Vorbereitung" in exc_info.value.detail


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
