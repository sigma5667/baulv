"""Tests for the v23.7 page_count extraction at upload time.

Pre-v23.7 the upload handler left ``Plan.page_count`` NULL until the
analysis step ran; that meant the plans-list UI rendered "? Seiten"
on every plan a basis user uploaded but couldn't analyse, which was
unprofessional. v23.7 reads ``doc.page_count`` from PyMuPDF inline
on upload so the field is populated before the user sees the row.

These tests pin two contracts:

  1. Successful upload of a real (synthetic) PDF persists the
     correct ``page_count`` on the ``Plan`` row.
  2. Upload of a corrupt / unreadable PDF doesn't crash — the
     handler logs a warning and persists ``page_count = NULL``,
     letting the analysis step recover later.

We don't unit-test PyMuPDF itself; the second test relies on the
exception path inside the upload handler being defensive enough
that a fitz failure is swallowed.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.plans import upload_plan
from app.db.models.plan import Plan
from app.db.models.project import Project
from app.db.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_project(db: AsyncSession) -> tuple[User, Project]:
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


def _make_pdf_bytes(num_pages: int = 3) -> bytes:
    """Build a minimal valid PDF with ``num_pages`` pages.

    PyMuPDF can render this even though it's hand-rolled — every
    page is empty but structurally valid (``%PDF-`` header, xref,
    trailer). Reusing fitz here means we don't have to ship a real
    PDF file in the test fixtures.
    """
    import fitz  # PyMuPDF

    doc = fitz.open()  # empty PDF
    for _ in range(num_pages):
        doc.new_page()
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_upload_file(filename: str, data: bytes) -> UploadFile:
    """Build an UploadFile whose ``file`` is a seekable BytesIO so
    the handler's magic-byte read + seek-back works exactly as it
    would behind the FastAPI multipart parser."""
    return UploadFile(
        filename=filename,
        file=io.BytesIO(data),
        headers={"content-type": "application/pdf"},
    )


# ---------------------------------------------------------------------------
# 1. Happy path — page_count populated from the PDF metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_persists_page_count(
    db_session: AsyncSession, tmp_path
):
    """A 3-page PDF gets ``page_count == 3`` on the resulting row."""
    user, project = await _seed_user_and_project(db_session)

    # Point ``settings.upload_dir`` at the tmp_path so the handler's
    # disk write lands somewhere we can clean up automatically.
    pdf_bytes = _make_pdf_bytes(num_pages=3)
    upload_file = _make_upload_file("test.pdf", pdf_bytes)

    with patch("app.api.plans.settings") as mock_settings:
        mock_settings.upload_path = Path(tmp_path)
        mock_settings.max_plan_file_mb = 25
        # Mirror the real config defaults; only the upload path
        # actually matters for this assertion.

        plan = await upload_plan(
            project_id=project.id,
            file=upload_file,
            plan_type="grundriss",
            user=user,
            db=db_session,
        )
        await db_session.commit()

    assert plan.page_count == 3, (
        f"Expected page_count=3 from a 3-page PDF, got {plan.page_count}"
    )

    # Round-trip through the DB to confirm persistence.
    fetched = await db_session.get(Plan, plan.id)
    assert fetched is not None
    assert fetched.page_count == 3


# ---------------------------------------------------------------------------
# 2. Fitz failure is swallowed → page_count stays NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_with_fitz_failure_does_not_crash(
    db_session: AsyncSession, tmp_path
):
    """If ``fitz.open`` raises, the upload still succeeds and
    ``page_count`` is NULL. The analysis step will recover later
    (the pipeline's own ``page_count`` write is the fallback)."""
    user, project = await _seed_user_and_project(db_session)

    # Pass a *valid* PDF (passes the magic-byte check) so we get to
    # the fitz call site, then mock fitz.open to raise.
    pdf_bytes = _make_pdf_bytes(num_pages=2)
    upload_file = _make_upload_file("broken.pdf", pdf_bytes)

    with patch("app.api.plans.settings") as mock_settings, patch(
        "fitz.open"
    ) as mock_fitz_open:
        mock_settings.upload_path = Path(tmp_path)
        mock_settings.max_plan_file_mb = 25
        mock_fitz_open.side_effect = RuntimeError("fitz exploded")

        plan = await upload_plan(
            project_id=project.id,
            file=upload_file,
            plan_type="grundriss",
            user=user,
            db=db_session,
        )
        await db_session.commit()

    # Upload still succeeded; page_count is NULL because fitz failed.
    assert plan.page_count is None
    assert plan.filename == "broken.pdf"
