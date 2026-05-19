"""Tests für das v24.4.3 ``Room.is_active``-Feature.

Vier Themenblöcke:

1. **Schema** — RoomCreate default ist ``True``, RoomUpdate ist optional,
   RoomResponse macht das Feld sichtbar. Lock per Pydantic-Roundtrip.

2. **DB-Model** — frisch erzeugter Raum hat ``is_active = True`` (server-
   default), und der Wert lässt sich auf ``False`` umschalten und wieder
   zurück. Stellt sicher dass die Migration den Default korrekt setzt
   und die Spalte NOT NULL bleibt.

3. **Aggregations-Filterung** — der ``calculation_engine``-Loader
   überspringt inaktive Räume, der ``mengenermittlung_pdf``-Renderer
   summiert nur aktive und führt inaktive in der separaten
   "Aus der Berechnung ausgenommen"-Sektion auf.

4. **MCP-Tool** — ``_room_summary`` exponiert ``is_active`` (Agents
   müssen die Info sehen, auch wenn das Tool selbst nicht filtert).

Wo eine echte ``AsyncSession`` notwendig ist, nutzen wir die
``db_session``-Fixture aus ``conftest.py`` (in-memory SQLite via
aiosqlite). Reine Schema-Validierung läuft direkt gegen Pydantic ohne
DB-Setup.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.calculation_engine.engine import load_rooms_for_project
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.export.mengenermittlung_pdf import export_mengenermittlung_pdf
from app.mcp.server import _room_summary
from app.schemas.room import RoomCreate, RoomResponse, RoomUpdate


# ---------------------------------------------------------------------------
# Helpers — local fork of test_mengenermittlung_pdf's seed pattern
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test User",
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_project_with_two_rooms(
    db: AsyncSession,
    *,
    user: User,
    second_room_is_active: bool = True,
) -> tuple[Project, Room, Room]:
    """Build Project → Building → Floor → Unit → 2 Rooms.

    First room is always active (Wohnzimmer). Second room is
    parameterised via ``second_room_is_active`` so a test can flip the
    Balkon between included and ausgenommen without re-implementing the
    whole seed.
    """
    project = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Wohnhaus Beispiel",
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

    active_room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Wohnzimmer",
        area_m2=Decimal("20.00"),
        perimeter_m=Decimal("18.00"),
        height_m=Decimal("2.50"),
        wall_area_gross_m2=Decimal("45.00"),
        wall_area_net_m2=Decimal("45.00"),
        is_active=True,
    )
    db.add(active_room)

    other_room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Balkon",
        area_m2=Decimal("4.00"),
        perimeter_m=Decimal("8.00"),
        height_m=Decimal("2.50"),
        wall_area_gross_m2=Decimal("20.00"),
        wall_area_net_m2=Decimal("20.00"),
        is_active=second_room_is_active,
    )
    db.add(other_room)

    await db.commit()
    return project, active_room, other_room


# ---------------------------------------------------------------------------
# Block 1 — Schema
# ---------------------------------------------------------------------------


def test_room_create_defaults_to_active():
    """Ohne explizite Angabe ist ein neuer Raum aktiv — sichert dass
    Vision-Pipeline-Räume nicht versehentlich stumm geschluckt werden."""
    payload = RoomCreate(name="Frisch")
    assert payload.is_active is True


def test_room_create_can_be_inactive_explicitly():
    """Caller kann Inaktiv explizit setzen (Imports etc.)."""
    payload = RoomCreate(name="Stillgelegt", is_active=False)
    assert payload.is_active is False


def test_room_update_is_active_field_is_optional():
    """RoomUpdate akzeptiert ``None`` für is_active. Das passt zum
    ``exclude_unset=True``-Pattern in ``PUT /rooms/{id}``: ohne
    explizites Update bleibt der Server-Wert unangetastet."""
    payload = RoomUpdate()
    assert payload.is_active is None

    payload_explicit = RoomUpdate(is_active=False)
    assert payload_explicit.is_active is False


def test_room_response_carries_is_active():
    """RoomResponse muss das Feld nach außen geben, sonst sieht das
    Frontend den Server-State nie."""
    data = {
        "id": uuid.uuid4(),
        "unit_id": uuid.uuid4(),
        "plan_id": None,
        "name": "Bad",
        "room_number": None,
        "room_type": "bad",
        "area_m2": 6.0,
        "perimeter_m": 10.0,
        "perimeter_source": "manual",
        "height_m": 2.5,
        "ceiling_height_source": "manual",
        "floor_type": "fliesen",
        "wall_type": None,
        "ceiling_type": None,
        "is_wet_room": True,
        "has_dachschraege": False,
        "is_staircase": False,
        "is_active": False,
        "source": "manual",
        "ai_confidence": None,
        "openings": [],
    }
    response = RoomResponse.model_validate(data)
    assert response.is_active is False


# ---------------------------------------------------------------------------
# Block 2 — DB-Model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_is_active_defaults_to_true_in_db(db_session: AsyncSession):
    """Ein Raum, der OHNE explizites ``is_active`` eingefügt wird, hat
    nach dem Refresh ``True``. Das deckt server_default ab — wenn das
    Default jemals von ``"true"`` auf ``"false"`` driftet, schlägt
    dieser Test sofort an."""
    user = await _seed_user(db_session)

    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db_session.add(project)
    await db_session.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="T1")
    db_session.add(unit)
    await db_session.flush()

    # Bewusst kein ``is_active=`` — wir wollen den Server-Default sehen.
    room = Room(id=uuid.uuid4(), unit_id=unit.id, name="Wohnzimmer")
    db_session.add(room)
    await db_session.commit()
    await db_session.refresh(room)

    assert room.is_active is True


@pytest.mark.asyncio
async def test_room_is_active_can_toggle(db_session: AsyncSession):
    """Toggle in beide Richtungen — sichert dass die Spalte ein
    normales beschreibbares Bool ist (kein read-only attribute)."""
    user = await _seed_user(db_session)
    _, _, balkon = await _seed_project_with_two_rooms(db_session, user=user)

    assert balkon.is_active is True
    balkon.is_active = False
    await db_session.commit()
    await db_session.refresh(balkon)
    assert balkon.is_active is False

    balkon.is_active = True
    await db_session.commit()
    await db_session.refresh(balkon)
    assert balkon.is_active is True


# ---------------------------------------------------------------------------
# Block 3 — Aggregations-Filterung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_rooms_for_project_excludes_inactive(
    db_session: AsyncSession,
):
    """``calculation_engine.load_rooms_for_project`` filtert inaktive
    Räume schon im DB-Query weg. Wenn dieser Filter wegfällt, fließen
    die ausgenommenen Räume wieder in die LV-Position-Generierung ein
    — das wäre eine stille Regression."""
    user = await _seed_user(db_session)
    project, _, _ = await _seed_project_with_two_rooms(
        db_session, user=user, second_room_is_active=False
    )

    rooms = await load_rooms_for_project(project.id, db_session)
    names = sorted(r.name for r in rooms)
    assert names == ["Wohnzimmer"]


@pytest.mark.asyncio
async def test_load_rooms_for_project_includes_active_only(
    db_session: AsyncSession,
):
    """Sanity — sind beide Räume aktiv, kommen auch beide zurück."""
    user = await _seed_user(db_session)
    project, _, _ = await _seed_project_with_two_rooms(
        db_session, user=user, second_room_is_active=True
    )

    rooms = await load_rooms_for_project(project.id, db_session)
    names = sorted(r.name for r in rooms)
    assert names == ["Balkon", "Wohnzimmer"]


@pytest.mark.asyncio
async def test_pdf_renders_excluded_section_when_inactive_room_exists(
    db_session: AsyncSession,
):
    """Inaktiver Raum erscheint NICHT in der Übersicht/Summen, ABER
    in der "Aus der Berechnung ausgenommen"-Sektion am Ende des PDFs.

    Search-Strategie: PDF-Bytes nach Latin-1 enkodierten Substrings
    durchsuchen. ReportLab embeddet Text uncompressed bei unseren
    Standard-Helvetica-Einstellungen, also greifen plain ``in``-Checks.
    Wir suchen nach dem unverkennbaren Section-Header.
    """
    user = await _seed_user(db_session)
    await _seed_project_with_two_rooms(
        db_session, user=user, second_room_is_active=False
    )

    # Re-fetch via load — der Render-Pfad lädt selbst.
    stmt = select(Project).where(Project.user_id == user.id)
    project = (await db_session.execute(stmt)).scalars().first()

    pdf_bytes = await export_mengenermittlung_pdf(
        project.id, db_session, creator=user
    )

    # Der Section-Header muss auftauchen.
    assert b"Aus der Berechnung ausgenommen" in pdf_bytes
    # Der Name des inaktiven Raums muss in der Transparenz-Liste
    # erscheinen (damit der PDF-Empfänger sieht, welcher Raum
    # ausgeklammert wurde).
    assert b"Balkon" in pdf_bytes


@pytest.mark.asyncio
async def test_pdf_skips_excluded_section_when_all_rooms_active(
    db_session: AsyncSession,
):
    """Ohne inaktive Räume gibt es keinen "Ausgenommen"-Block — der
    soll sich nicht als leere Sektion zwischen die Summen und den
    Footer drängen."""
    user = await _seed_user(db_session)
    await _seed_project_with_two_rooms(
        db_session, user=user, second_room_is_active=True
    )

    stmt = select(Project).where(Project.user_id == user.id)
    project = (await db_session.execute(stmt)).scalars().first()

    pdf_bytes = await export_mengenermittlung_pdf(
        project.id, db_session, creator=user
    )

    assert b"Aus der Berechnung ausgenommen" not in pdf_bytes


# ---------------------------------------------------------------------------
# Block 4 — MCP-Tool
# ---------------------------------------------------------------------------


def test_mcp_room_summary_exposes_is_active():
    """``_room_summary`` muss ``is_active`` zurückgeben. Agents brauchen
    die Information, um zu wissen, welche Räume der User ausgeklammert
    hat — selbst wenn das MCP-Tool selbst NICHT filtert."""
    room = Room(
        id=uuid.uuid4(),
        unit_id=uuid.uuid4(),
        name="Balkon",
        is_active=False,
        ceiling_height_source="default",
        deductions_enabled=True,
        is_wet_room=False,
        is_staircase=False,
        has_dachschraege=False,
    )
    # ``openings`` ist None bei einem nicht-DB-gemounteten Room — der
    # Helper handelt das via ``if room.openings is not None`` ab.
    room.openings = []
    summary = _room_summary(room)

    assert summary["is_active"] is False
    # Defensiv: andere Felder bleiben unverändert.
    assert summary["name"] == "Balkon"
    assert summary["is_staircase"] is False
