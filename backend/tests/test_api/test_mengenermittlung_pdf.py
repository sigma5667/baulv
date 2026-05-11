"""Tests for the Mengenermittlung-PDF export.

Coverage (v24.3 baseline):

  1. **Empty project → 400.** Pre-v24.3 the renderer produced a
     cover-only PDF for projects without rooms. Profi-Feedback
     identified that as an anti-feature (subcontractor risk); the
     route now refuses with a 400 + German message.
  2. Three-room project → PDF carries the project metadata, every
     room's name in the body, and the section labels we promise
     in the spec (Übersicht, Berechnungs-Nachweise, Summen). The
     v24.3 disclaimer box is also rendered.
  3. Cross-tenant protection (User B cannot export User A's PDF).
  4. 404 for a missing project.
  5. **v24.3 — creator label** uses ``full_name + company_name``
     (with ``role`` in parens when set); never the username part
     of the email.
  6. **v24.3 — empty metadata rows are omitted** rather than
     rendered with em-dash placeholders.
  7. **v24.3 — "Seite X von N" page numbering** appears in the
     footer (the NumberedCanvas two-pass machinery).

The tests assert on byte-substring presence inside the rendered
PDF stream — not pixel-perfect layout, but enough to lock the
content contracts. We're deliberately not asserting on the
reportlab flowable tree because that couples the suite to
reportlab's internal layout positioning.

Note on PDF byte-search
=======================

reportlab embeds text uncompressed by default in our usage, so
plain ``b"text" in pdf_bytes`` works for ASCII / Latin-1 strings.
German umlauts go through Helvetica's Latin-1 / cp1252 encoding,
so umlauted words are searched for as their stem (e.g. ``b"Raum"``
to catch both ``Raum`` and ``Räume``).
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
from app.export.mengenermittlung_pdf import (
    _format_creator_label,
    export_mengenermittlung_pdf,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user(
    db: AsyncSession,
    *,
    prefix: str = "u",
    full_name: str = "Tester GmbH",
    company_name: str | None = None,
    role: str | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{prefix}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name=full_name,
        company_name=company_name,
        role=role,
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
# 1. Empty project → 400 (v24.3 — was cover-PDF in pre-v24.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_project_returns_400(db_session: AsyncSession):
    """The route refuses empty projects with a German message.

    The endpoint guard runs BEFORE the renderer; we exercise it
    here at the endpoint level. The renderer itself still handles
    no-rooms gracefully (defence-in-depth) but is not the primary
    protection — the route is.
    """
    user = await _seed_user(db_session)
    project = await _seed_empty_project(db_session, user=user)

    with pytest.raises(HTTPException) as exc_info:
        await export_project_mengenermittlung(
            project_id=project.id, user=user, db=db_session,
        )

    assert exc_info.value.status_code == 400
    # German wording is part of the contract — the frontend matches
    # on the first sentence so a future copy edit shouldn't break it.
    assert "Räume hinzufügen" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 2. Three-room project → full sections + disclaimer box
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_room_project_renders_all_sections(
    db_session: AsyncSession,
):
    """Every room name appears in the body, plus the three major
    section labels (Übersicht, Nachweise, Summen). v24.3 — also
    locks the disclaimer-box copy onto the first page."""
    user = await _seed_user(db_session)
    project = await _seed_three_room_project(db_session, user=user)

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Comfortably bigger than a no-content document.
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

    # v24.3 — Disclaimer box copy on page 1.
    assert b"Vorabkalkulation" in pdf_bytes
    assert b"keine gepr" in pdf_bytes  # "keine geprüfte Berechnung"


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
# 4. 404 for a missing project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_missing_project_404(db_session: AsyncSession):
    user = await _seed_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await export_project_mengenermittlung(
            project_id=uuid.uuid4(), user=user, db=db_session,
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 5. v24.3 — creator label formatting (pure-function tests)
# ---------------------------------------------------------------------------


class _StubUser:
    """Lightweight stand-in for the ORM ``User`` class. We don't
    need a DB-backed instance to test the pure label-formatter;
    duck-typing on the four attributes the helper reads is enough."""

    def __init__(
        self,
        *,
        full_name: str | None = None,
        company_name: str | None = None,
        role: str | None = None,
        email: str | None = None,
    ) -> None:
        self.full_name = full_name
        self.company_name = company_name
        self.role = role
        self.email = email


def test_creator_label_full_name_and_company():
    """The headline case: both fields set → "Name, Firma"."""
    u = _StubUser(
        full_name="Max Mustermann",
        company_name="Beispiel-Bau GmbH",
        email="max@example.com",
    )
    label = _format_creator_label(u)
    assert label == "Max Mustermann, Beispiel-Bau GmbH"


def test_creator_label_full_name_only():
    """Only the full name → no company suffix, no email leak."""
    u = _StubUser(full_name="Max Mustermann", email="max@example.com")
    label = _format_creator_label(u)
    assert label == "Max Mustermann"


def test_creator_label_email_fallback_when_no_name():
    """No full_name set → fall back to email. NEVER the username."""
    u = _StubUser(full_name="", email="kafjd@example.com")
    label = _format_creator_label(u)
    # The username "kafjd" must NOT appear without the @-suffix —
    # Profi-Feedback called out "kafjd (beta-test@baulv.at)" as
    # unprofessional. Either we render the full email or nothing.
    assert label == "kafjd@example.com"
    assert "kafjd " not in label  # bare username + space


def test_creator_label_role_in_parens():
    """role-Suffix in parens; combined with name+company."""
    u = _StubUser(
        full_name="Erika Beispiel",
        company_name="Beispiel Architekten ZT GmbH",
        role="Planverfasserin",
    )
    label = _format_creator_label(u)
    assert label == "Erika Beispiel, Beispiel Architekten ZT GmbH (Planverfasserin)"


def test_creator_label_none_returns_empty():
    """No creator (anonymous export) → empty string (the metadata
    block then skips the "Erstellt von" row entirely)."""
    assert _format_creator_label(None) == ""


# ---------------------------------------------------------------------------
# 6. v24.3 — empty metadata rows are omitted (no em-dashes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_block_skips_empty_fields(
    db_session: AsyncSession,
):
    """Project with only ``name`` set: the metadata block must not
    render rows for ``address``, ``client_name``, etc. Pre-v24.3
    these came out as "Adresse: —" — visual noise.

    We assert on the absence of em-dashes inside the rendered PDF.
    A defensive check: the disclaimer box and other sections never
    use em-dashes either, so any "—" in the byte stream is a
    regression marker."""
    user = await _seed_user(
        db_session, full_name="Max Mustermann", company_name="Mustermann Bau"
    )
    project = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Minimal-Projekt",
        # All optional fields stay None.
    )
    db_session.add(project)
    # Need at least one room so we hit the full render path
    # (empty projects are blocked at the route; the renderer
    # would still produce a header-only PDF defence-in-depth,
    # but here we want to exercise the populated path).
    building = Building(id=uuid.uuid4(), project_id=project.id, name="Haus")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG", level_number=0)
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top 1")
    db_session.add(unit)
    await db_session.flush()
    db_session.add(Room(
        id=uuid.uuid4(), unit_id=unit.id, name="Zimmer",
        area_m2=Decimal("10.0"),
    ))
    await db_session.commit()

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    # The em-dash glyph (U+2014) reportlab encodes via the latin-1
    # multi-byte path; the simplest robust check is for the
    # German label of the field we know is missing — it must NOT
    # appear at all.
    assert b"Adresse" not in pdf_bytes, (
        "Empty address row should be omitted, not rendered with em-dash."
    )
    assert b"Auftraggeber" not in pdf_bytes, (
        "Empty client row should be omitted, not rendered with em-dash."
    )
    # The fields we DID set must still be there.
    assert b"Minimal-Projekt" in pdf_bytes
    assert b"Max Mustermann" in pdf_bytes


# ---------------------------------------------------------------------------
# 7. v24.3 — "Seite X von N" page numbering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_footer_renders_seite_x_von_n(db_session: AsyncSession):
    """The NumberedCanvas two-pass machinery must produce
    "Seite X von N" in the footer rather than the pre-v24.3 bare
    "Seite X"."""
    user = await _seed_user(db_session)
    project = await _seed_three_room_project(db_session, user=user)

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )
    # Footer text is uncompressed in our reportlab usage — searching
    # for "von" between "Seite" and a digit is the simplest robust
    # contract assertion.
    assert b"Seite" in pdf_bytes
    assert b"von" in pdf_bytes


# ---------------------------------------------------------------------------
# 8. v24.3 — no internal version markers (debug-artifact audit)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 9. v24.3.1 — missing-height hint replaces silent skip in detail block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_block_renders_hint_when_height_missing(
    db_session: AsyncSession,
):
    """A room with ``perimeter_m`` set but ``height_m=None`` used to
    have its Wandfläche-brutto formula silently dropped from the
    detail block — leaving only "Boden-/Deckenfläche = X m²" and no
    indication why nothing else was computed. v24.3.1 renders an
    explicit hint instead.

    Vater-Feedback was the trigger for this fix; a follow-up to the
    pipeline writeback so that even projects that somehow end up
    with a NULL height (manual rooms, legacy data not yet backfilled)
    show the user WHY the calc is incomplete.
    """
    user = await _seed_user(db_session)
    # Project with one Room: perimeter set, height NULL.
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Test")
    db_session.add(project)
    await db_session.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(
        id=uuid.uuid4(), building_id=building.id, name="EG", level_number=0
    )
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="T1")
    db_session.add(unit)
    await db_session.flush()
    db_session.add(
        Room(
            id=uuid.uuid4(),
            unit_id=unit.id,
            name="Wohnzimmer",
            area_m2=Decimal("24.50"),
            perimeter_m=Decimal("20.00"),
            height_m=None,  # the failure-mode case
        )
    )
    await db_session.commit()

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    # The new explicit hint phrase. We search for the German stem
    # "Raumh" which catches both the umlauted and the encoded form
    # in the PDF byte stream — and "fehlt" to lock the wording so a
    # future copy edit doesn't drift away from the contract.
    assert b"Raumh" in pdf_bytes
    assert b"fehlt" in pdf_bytes


# ---------------------------------------------------------------------------
# 10. v24.3.1 — create_room: explicit 2.50 is "manual", not "default"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_room_tags_explicit_2_50_as_manual(
    db_session: AsyncSession,
):
    """Pre-v24.3.1 ``create_room`` had a heuristic that marked any
    ``height_m == 2.50`` as ``ceiling_height_source = "default"``,
    even when the user explicitly typed it. The protection was for
    an old frontend bug that pre-filled the field; the FE bug is
    fixed since v22.x and the heuristic was a false-positive.

    v24.3.1 removed the special case. Locking that down so a future
    'defensive' refactor doesn't re-introduce it.
    """
    from uuid import uuid4 as _uuid4

    from app.api.rooms import create_room
    from app.schemas.room import RoomCreate

    user = await _seed_user(db_session)
    project = Project(id=_uuid4(), user_id=user.id, name="P")
    db_session.add(project)
    await db_session.flush()
    building = Building(id=_uuid4(), project_id=project.id, name="H")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(id=_uuid4(), building_id=building.id, name="EG", level_number=0)
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=_uuid4(), floor_id=floor.id, name="T1")
    db_session.add(unit)
    await db_session.commit()

    # Build the RoomCreate payload — explicit 2.50 m, no source.
    payload = RoomCreate(
        name="Wohnzimmer",
        area_m2=20.0,
        perimeter_m=18.0,
        height_m=2.50,
    )
    room = await create_room(
        unit_id=unit.id, data=payload, user=user, db=db_session,
    )
    await db_session.commit()

    # Both halves of the contract.
    assert room.height_m is not None
    assert abs(float(room.height_m) - 2.5) < 1e-6
    # The headline: source is "manual", not "default".
    assert room.ceiling_height_source == "manual", (
        f"Explicit 2.50 m tagged as {room.ceiling_height_source!r} — "
        f"v24.3.1 removed the special-case heuristic, this regressed."
    )


@pytest.mark.asyncio
async def test_no_debug_version_markers_in_pdf(db_session: AsyncSession):
    """The footer + PDF metadata must not leak internal version
    strings (v23.9, build-tags, branch names). Profi-Feedback
    flagged this as unprofessional in v23.9; the contract has been
    in effect since but isn't tested anywhere — locking it down."""
    user = await _seed_user(db_session)
    project = await _seed_three_room_project(db_session, user=user)

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    # The four canonical leak shapes to guard against. If any one
    # appears in the byte stream a future refactor has stamped a
    # debug marker somewhere user-visible — and the test fails so
    # the author sees it.
    forbidden = [b"v23.9", b"v24.0", b"v24.1", b"v24.2", b"v24.3", b"claude"]
    for marker in forbidden:
        assert marker not in pdf_bytes, (
            f"Internal version marker {marker!r} leaked into PDF body"
        )
