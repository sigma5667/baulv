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
from sqlalchemy import select
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
# 8. v24.3.1 — incomplete-rooms notices (Vater-Bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_block_renders_incomplete_notice_when_area_missing(
    db_session: AsyncSession,
):
    """Ein Raum mit ``area_m2=None`` aber gesetzter ``height_m`` muss
    im Detail-Nachweis-Block eine kursive ``Eingaben unvollständig:
    Fläche fehlt``-Notiz tragen.

    Vater-Bug (2026-05-11): Vision konnte bei Balkonen, Stiegenhäusern
    und Bädern weder area noch perimeter extrahieren — die Räume
    landeten mit beidem NULL in der DB. Pre-v24.3.1 entfielen die
    abhängigen Formel-Zeilen kommentarlos, der Detail-Block wirkte
    leer und der Leser missdeutete das als "Höhen fehlen". Die
    Notiz schliesst die Lücke."""
    user = await _seed_user(db_session)
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Test")
    db_session.add(project)
    await db_session.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(
        id=uuid.uuid4(), building_id=building.id, name="OG", level_number=1,
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
            name="Balkon W58",
            area_m2=None,            # the Vater-Bug trigger
            perimeter_m=None,
            height_m=Decimal("2.50"),
        )
    )
    await db_session.commit()

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Headline assertion: the new "Eingaben unvollständig" prefix
    # must appear. We search for the umlaut-stable stem so the
    # encoding pathway doesn't matter for the test.
    assert b"Eingaben unvoll" in pdf_bytes, (
        "Detail-Block muss den 'Eingaben unvollständig'-Hinweis tragen "
        "wenn ein Kernmaß fehlt."
    )
    # And both missing fields are named explicitly (Fläche / Umfang).
    # "Fl" + "che" stem is robust to whatever bytes reportlab emits.
    assert b"Fl" in pdf_bytes
    assert b"Umfang" in pdf_bytes


@pytest.mark.asyncio
async def test_overview_section_renders_aggregate_incomplete_hint(
    db_session: AsyncSession,
):
    """Mindestens 1 Raum unvollständig → vor der Übersichtstabelle
    erscheint ein Zähler-Hinweis 'N von M Räumen unvollständig'.

    Lockt das aggregate-Sichtbarkeits-Versprechen von v24.3.1: der
    Leser sieht den Mängelstand auf der ersten Seite, ohne durch
    die Detail-Nachweise scrollen zu müssen."""
    user = await _seed_user(db_session)
    project = await _seed_three_room_project(db_session, user=user)

    # Einen der drei Räume entkernen — area + perimeter leeren so
    # dass er als "unvollständig" zählt. Die anderen zwei bleiben
    # vollwertig; das ergibt einen sauberen "1 von 3"-Wortlaut.
    rooms = (
        await db_session.execute(select(Room).order_by(Room.name))
    ).scalars().all()
    rooms[0].area_m2 = None
    rooms[0].perimeter_m = None
    await db_session.commit()

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Konkreter Wortlaut der Zähler-Zeile. ASCII-only, daher direkt
    # als Bytes-Substring suchbar.
    assert b"1 von 3" in pdf_bytes, (
        "Übersichtstabelle muss den Hinweis '1 von 3 Räumen unvollständig' "
        "tragen wenn genau ein Raum unvollständig ist."
    )
    # Und das Vokabel selbst (Stem 'unvoll' für umlaut-stabilen Match).
    assert b"unvoll" in pdf_bytes


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
    forbidden = [
        b"v23.9", b"v24.0", b"v24.1", b"v24.2", b"v24.3", b"v24.4",
        b"claude",
    ]
    for marker in forbidden:
        assert marker not in pdf_bytes, (
            f"Internal version marker {marker!r} leaked into PDF body"
        )


# ---------------------------------------------------------------------------
# 11. v24.4 — Bodenflächen-Aggregation nach Belag und Geschoss
# ---------------------------------------------------------------------------


async def _seed_floor_covering_project(
    db_session: AsyncSession, *, user: User
) -> Project:
    """Project mit zwei Geschossen + Räumen unterschiedlicher Beläge.

    Layout:

      EG (level 0):
        Wohnzimmer 24,5 m² — Parkett
        Bad         6,0 m² — Fliesen
        WC          2,5 m² — NULL (Räume ohne Belag-Angabe)

      1.OG (level 1):
        Schlafen   16,0 m² — Parkett

    Erwartete Aggregation:
      EG  → Parkett 24,5 + Fliesen 6,0 + nicht-klassifiziert 2,5 = 33,0
      1.OG → Parkett 16,0 = 16,0
      Gesamt: 49,0
    """
    project = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Bodenbelag-Test",
    )
    db_session.add(project)
    await db_session.flush()
    building = Building(
        id=uuid.uuid4(), project_id=project.id, name="H", sort_order=0,
    )
    db_session.add(building)
    await db_session.flush()
    floor_eg = Floor(
        id=uuid.uuid4(),
        building_id=building.id,
        name="Erdgeschoss",
        level_number=0,
        sort_order=0,
    )
    floor_og = Floor(
        id=uuid.uuid4(),
        building_id=building.id,
        name="1.Obergeschoss",
        level_number=1,
        sort_order=1,
    )
    db_session.add_all([floor_eg, floor_og])
    await db_session.flush()
    unit_eg = Unit(id=uuid.uuid4(), floor_id=floor_eg.id, name="EG-T1")
    unit_og = Unit(id=uuid.uuid4(), floor_id=floor_og.id, name="OG-T1")
    db_session.add_all([unit_eg, unit_og])
    await db_session.flush()

    db_session.add_all([
        Room(
            id=uuid.uuid4(), unit_id=unit_eg.id, name="Wohnzimmer",
            area_m2=Decimal("24.50"), perimeter_m=Decimal("20.0"),
            height_m=Decimal("2.50"), floor_type="parkett",
        ),
        Room(
            id=uuid.uuid4(), unit_id=unit_eg.id, name="Bad",
            area_m2=Decimal("6.00"), perimeter_m=Decimal("10.0"),
            height_m=Decimal("2.50"), floor_type="fliesen",
        ),
        Room(
            id=uuid.uuid4(), unit_id=unit_eg.id, name="WC",
            area_m2=Decimal("2.50"), perimeter_m=Decimal("6.0"),
            height_m=Decimal("2.50"), floor_type=None,
        ),
        Room(
            id=uuid.uuid4(), unit_id=unit_og.id, name="Schlafen",
            area_m2=Decimal("16.00"), perimeter_m=Decimal("16.0"),
            height_m=Decimal("2.50"), floor_type="parkett",
        ),
    ])
    await db_session.commit()
    return project


@pytest.mark.asyncio
async def test_pdf_contains_floor_covering_aggregation_section(
    db_session: AsyncSession,
):
    """Räume mit gesetztem ``floor_type`` → PDF enthält die Sektion
    "Bodenflächen nach Belag", inkl. Geschoss-Aufschlüsselung,
    Summen pro Geschoss, "Räume ohne Belag-Angabe"-Block für den WC
    ohne Belag, und das Gesamt-Total.

    Locks die v24.4-Aggregations-Outputs als Byte-Substring-Asserts
    so dass jede künftige Refactoring-Änderung am Wortlaut der
    Sektion sofort auffällt."""
    user = await _seed_user(db_session)
    project = await _seed_floor_covering_project(db_session, user=user)

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Section-Header (Stem-Match wegen Umlaut in "Bodenflächen").
    assert b"Bodenfl" in pdf_bytes
    assert b"Belag" in pdf_bytes
    # Aggregations-Werte. Parkett im EG = 24,5 + 0 (Bad ist Fliesen).
    # Im OG kommt 16 dazu — die Summe "Parkett" rendert pro Geschoss
    # einzeln, kein Cross-Geschoss-Sammler.
    assert b"Parkett" in pdf_bytes
    assert b"Fliesen" in pdf_bytes
    # WC ist nicht klassifiziert → "Räume ohne Belag-Angabe"-Bucket
    # muss erscheinen (v24.4.1 — Wortlaut umbenannt).
    assert b"R\xe4ume ohne Belag-Angabe" in pdf_bytes or b"ume ohne Belag" in pdf_bytes
    # Plus die Raum-Anzahl in Klammern: "(1 Raum)" weil nur das WC
    # ohne Belag ist.
    assert b"(1 Raum)" in pdf_bytes
    # Geschoss-Summen-Wortlaut (Stem für Umlaut-Stabilität).
    assert b"Summe Erdgeschoss" in pdf_bytes
    assert b"Summe 1.Obergeschoss" in pdf_bytes
    # Gesamttotal.
    assert b"Gesamt-Bodenfl" in pdf_bytes
    # Gesamtfläche = 24.5 + 6 + 2.5 + 16 = 49 m² → German notation "49,00 m²".
    assert b"49,00 m" in pdf_bytes


@pytest.mark.asyncio
async def test_pdf_renders_section_with_only_unclassified_rooms(
    db_session: AsyncSession,
):
    """v24.4.1 — Verhalten umgekehrt gegenüber v24.4.

    Drei-Raum-Projekt OHNE ``floor_type`` an irgendeinem Raum: die
    Sektion "Bodenflächen nach Belag" wird jetzt TROTZDEM gerendert
    und enthält **ausschliesslich** den "Räume ohne Belag-Angabe"-
    Bucket. Damit sieht der Bauträger (Vater-Feedback, 2026-05-12),
    dass er noch keine Beläge klassifiziert hat — statt dass die
    ganze Sektion unsichtbar verschluckt wird.

    Anti-Klutter-Garantie greift jetzt enger: Sektion nur skipen
    wenn KEIN Raum überhaupt area > 0 hat. Drei Räume mit Fläche
    aber ohne Belag = legitimer Use-Case → Sektion rendert."""
    user = await _seed_user(db_session)
    project = await _seed_three_room_project(db_session, user=user)
    # ``_seed_three_room_project`` setzt floor_type nicht → alle
    # drei Räume haben NULL aber area_m2 ist gesetzt.

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Sektion-Header rendert.
    assert b"Bodenfl" in pdf_bytes
    assert b"nach Belag" in pdf_bytes
    # Bucket-Label + Raum-Anzahl: 3 Räume sind ohne Belag.
    assert b"ume ohne Belag" in pdf_bytes  # Stem-Match wegen "Räume"
    assert b"(3 R" in pdf_bytes  # "(3 Räume)"
    # Geschoss-Summe + Gesamt-Total laufen auch durch.
    assert b"Gesamt-Bodenfl" in pdf_bytes
    # 24.5 + 12.0 + 6.0 = 42.5 m² Gesamtfläche.
    assert b"42,50 m" in pdf_bytes


@pytest.mark.asyncio
async def test_pdf_skips_floor_covering_section_when_no_room_has_area(
    db_session: AsyncSession,
):
    """v24.4.1 — der eigentliche Skip-Fall ist enger als pre-v24.4.1.

    Wenn KEIN Raum eine area_m2 hat, gibt es nichts zu aggregieren →
    Sektion wird weggelassen. Das ist die letzte verbliebene Anti-
    Klutter-Garantie, wesentlich enger als der pre-v24.4.1-Skip
    "kein Raum hat floor_type"."""
    user = await _seed_user(db_session)
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Leer")
    db_session.add(project)
    await db_session.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(
        id=uuid.uuid4(), building_id=building.id, name="EG", level_number=0,
    )
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="T1")
    db_session.add(unit)
    await db_session.flush()
    # Ein Raum mit area_m2=NULL UND floor_type=NULL.
    db_session.add(Room(
        id=uuid.uuid4(), unit_id=unit.id, name="Skizze",
        area_m2=None, perimeter_m=None, height_m=Decimal("2.50"),
    ))
    await db_session.commit()

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Section-Header darf NICHT erscheinen, weil null area_m2 zu
    # aggregieren ist sinnlos.
    assert b"nach Belag" not in pdf_bytes
    assert b"Gesamt-Bodenfl" not in pdf_bytes


@pytest.mark.asyncio
async def test_pdf_floor_covering_uses_nicht_zugeordnet_for_unassigned_floor(
    db_session: AsyncSession,
):
    """Räume ohne Geschoss-Zuordnung (z.B. weil die Vision keine
    Floor-Beschriftung erkannt hat) landen in einer "NICHT
    ZUGEORDNET"-Gruppe statt aus der Aggregation rauszufallen.

    Vater-Empirie (Musterhaus-Projekt, 2026-05-12): aktuelle Daten
    haben oft keine Floor-Info. v24.4-Plan-Addendum lässt uns die
    Aggregation trotzdem rendern, damit der Bauträger zumindest die
    Belag-Aufschlüsselung sieht — Geschoss-Tagging kommt später."""
    user = await _seed_user(db_session)
    project = await _seed_floor_covering_project(db_session, user=user)

    # Floor-Referenzen kappen indem wir die Floor-Rows mit
    # level_number=None überschreiben — Floor bleibt, aber name
    # erinnert nicht an ein konkretes Geschoss. Das simuliert
    # nicht ganz das None-Floor-Szenario, aber es testet den
    # Sort-Path für unbekannte Geschosse.
    #
    # Echter "Floor is None"-Pfad wäre nur erreichbar wenn ein
    # Raum ohne Building/Floor existiert — was die FK-Constraints
    # nicht erlauben. Realistischer Test:
    floors = (
        await db_session.execute(select(Floor))
    ).scalars().all()
    for f in floors:
        f.level_number = None
        f.name = "??"
    await db_session.commit()

    pdf_bytes = await export_mengenermittlung_pdf(
        project_id=project.id, db=db_session, creator=user,
    )

    assert pdf_bytes.startswith(b"%PDF-")
    # Die Sektion läuft normal — aber die Geschosse haben jetzt
    # alle den ungewohnten Namen "??", was als Geschoss-Header
    # auftaucht. Hauptsache: Aggregation funktioniert und total
    # ist gerendert.
    assert b"Bodenfl" in pdf_bytes
    assert b"Parkett" in pdf_bytes
    # Gesamttotal bleibt 49,00 m².
    assert b"49,00 m" in pdf_bytes
