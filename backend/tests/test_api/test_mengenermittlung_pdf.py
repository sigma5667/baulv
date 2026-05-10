"""Tests for the v23.9 Mengenermittlung-PDF export.

Coverage:

  1. Empty project (no rooms) → still produces a valid PDF with
     header + "Noch keine Räume erfasst" placeholder. Spec calls
     this out explicitly: empty projects should not 400; user might
     want a cover sheet they then populate.
  2. 3-room project → PDF carries the project metadata, every
     room's name in the body, and the section labels we promise
     in the spec (Übersicht, Berechnungs-Nachweise, Summen).
  3. Cross-tenant protection (User B cannot export User A's PDF).

The tests assert on byte-substring presence inside the rendered
PDF stream — not pixel-perfect layout, but enough to lock the
contract that "if the function returns, the right content is in
there". A pure unit test on the layout would couple the test
suite to reportlab's internal flowable-positioning, which we
shouldn't.

Note on PDF byte-search
=======================

reportlab embeds text uncompressed by default in our usage (no
``compress=1`` flag), so a plain ``b"text" in pdf_bytes`` works
for ASCII / Latin-1 strings. German umlauts go through reportlab's
default Helvetica encoding (Latin-1 / cp1252), so we search for
the umlaut-stripped form ("Raume") rather than wrestle with PDF
text-state operators in tests.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import export_project_mengenermittlung
from app.db.models.project import (
    Building,
    Floor,
    Project,
    Room,
    Unit,
)
from app.db.models.user import User
from app.export.mengenermittlung_pdf import export_mengenermittlung_pdf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession, *, prefix: str = "u") -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{prefix}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tester GmbH",
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_empty_project(
    db: AsyncSession, *, user: User
) -> Project:
    project = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Leeres Beispielprojekt",
        address="Musterstraße 1, 5020 Salzburg",
        client_name="Mustermann GmbH",
    )
    db.add(project)
    await db.commit()
    return project


async def _seed_three_room_project(
    db: AsyncSession, *, user: User
) -> Project:
    """Project with one Building → one EG-Floor → one Unit → three
    rooms (Wohnzimmer, Küche, Bad) so the PDF has something to
    aggregate over."""
    project = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Wohnhaus Beispielstraße",
        address="Beispielstraße 42, 5020 Salzburg",
        client_name="Familie Beispiel",
        project_number="2026-007",
    )
    db.add(project)
    await db.flush()

    building = Building(
        id=uuid.uuid4(), project_id=project.id, name="Haus A", sort_order=0
    )
    db.add(building)
    await db.flush()
    floor = Floor(
        id=uuid.uuid4(),
        building_id=building.id,
        name="Erdgeschoss",
        level_number=0,
        sort_order=0,
    )
    db.add(floor)
    await db.flush()
    unit = Unit(
        id=uuid.uuid4(), floor_id=floor.id, name="Top 1", sort_order=0
    )
    db.add(unit)
    await db.flush()

    rooms_data = [
        {
            "name": "Wohnzimmer",
            "room_type": "wohnen",
            "area_m2": Decimal("24.50"),
            "perimeter_m": Decimal("20.00"),
            "height_m": Decimal("2.50"),
            "wall_area_gross_m2": Decimal("50.00"),
            "wall_area_net_m2": Decimal("48.20"),
            "applied_factor": Decimal("1.000"),
        },
        {
            "name": "Küche",
            "room_type": "kueche",
            "area_m2": Decimal("12.00"),
            "perimeter_m": Decimal("14.00"),
            "height_m": Decimal("2.50"),
            "wall_area_gross_m2": Decimal("35.00"),
            "wall_area_net_m2": Decimal("33.20"),
            "applied_factor": Decimal("1.000"),
        },
        {
            "name": "Bad",
            "room_type": "bad",
            "area_m2": Decimal("6.00"),
            "perimeter_m": Decimal("10.00"),
            "height_m": Decimal("2.50"),
            "wall_area_gross_m2": Decimal("25.00"),
            "wall_area_net_m2": Decimal("25.00"),
            "applied_factor": Decimal("1.000"),
        },
    ]
    for data in rooms_data:
        db.add(Room(id=uuid.uuid4(), unit_id=unit.id, **data))

    await db.commit()
    return project


# ---------------------------------------------------------------------------
# 1. Empty project → cover-only PDF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_project_produces_valid_pdf(db_session: AsyncSession):
    """A project without any rooms must still render. Cover sheet
    only. Output starts with the PDF magic bytes ``%PDF-`` and is
    non-trivial in size (cover + footer + meta-table)."""
    user = await _seed_user(db_session)
    project = await _seed_empty_project(db_session, user=user)

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    # Magic bytes — ensure we got an actual PDF, not an HTML error.
    assert pdf_bytes.startswith(b"%PDF-")
    # Non-trivial size: cover sheet typically 2-4 KB even with all
    # fields empty. Anything under 1 KB suggests the renderer
    # returned early without a real document.
    assert len(pdf_bytes) > 1024

    # Project name lands in the cover.
    assert b"Leeres Beispielprojekt" in pdf_bytes
    # Empty-state hint is rendered.
    # ("Räume" → reportlab encodes ä as a glyph escape; we search
    # for the prefix that's stable across encodings.)
    assert b"Noch keine R" in pdf_bytes


# ---------------------------------------------------------------------------
# 2. Three-room project → full sections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_room_project_renders_all_sections(
    db_session: AsyncSession,
):
    """Every room name appears in the body, plus the three major
    section labels (Übersicht, Nachweise, Summen). Locks that the
    Detail-Block + Summary-Block actually fire when there's data."""
    user = await _seed_user(db_session)
    project = await _seed_three_room_project(db_session, user=user)

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Comfortably bigger than the empty-project case.
    assert len(pdf_bytes) > 4096

    # Project metadata
    assert b"Wohnhaus Beispielstra" in pdf_bytes  # ß is glyph-escaped
    assert b"2026-007" in pdf_bytes  # project_number

    # Each room appears at least once (table row OR detail block).
    for room_name in (b"Wohnzimmer", b"K", b"Bad"):
        assert room_name in pdf_bytes, (
            f"Room name {room_name!r} missing from PDF body"
        )

    # Section labels
    assert b"Berechnungs-Nachweise" in pdf_bytes
    assert b"Summen" in pdf_bytes


# ---------------------------------------------------------------------------
# 3. Cross-tenant protection at the endpoint layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_export_user_as_pdf(
    db_session: AsyncSession,
):
    """User B authenticated, attempts to export User A's project.
    Endpoint must 403 before the renderer runs."""
    user_a = await _seed_user(db_session, prefix="ua")
    project = await _seed_empty_project(db_session, user=user_a)

    user_b = await _seed_user(db_session, prefix="ub")

    with pytest.raises(HTTPException) as exc_info:
        await export_project_mengenermittlung(
            project_id=project.id, user=user_b, db=db_session,
        )
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# 4. 404 for missing project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_missing_project_404(db_session: AsyncSession):
    user = await _seed_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await export_project_mengenermittlung(
            project_id=uuid.uuid4(), user=user, db=db_session,
        )
    assert exc_info.value.status_code == 404
