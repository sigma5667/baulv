"""Regression test: sync-wall-areas must NOT clobber Leibung positions.

Bug: ``sync_wall_areas`` fans the total net wall area into every
position whose kurztext/langtext contains a wall keyword. The keyword
list includes ``"anstrich"``, so a Leibung (reveal) position named
"Leibungsbeschichtung … 2× Anstrich" (Gruppe "Leibungsanstrich") was
matched as a wall position and had its correct, much smaller value
(opening-perimeter × reveal-depth, e.g. 35.96 m²) overwritten with the
full net wall area (1071.27 m²). That's fachlich falsch — a reveal is
not the wall surface.

Fix (Variante B): an explicit ``_is_leibung_position`` skip step at the
top of the positions loop. This test drives the REAL ``sync_wall_areas``
endpoint against an in-memory SQLite DB and asserts:

  * the Leibung position keeps its value (35.96)         — the fix
  * the wall position is set to the wall total (1071.27) — unchanged
  * the ceiling position is set to the ceiling total (489.05) — unchanged

It also pins the three classifier helpers so a future refactor can't
silently re-route a Leibung row back onto the wall path.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lv import (
    sync_wall_areas,
    _is_ceiling_position,
    _is_leibung_position,
    _is_wall_position,
)
from app.db.models.lv import (
    Leistungsgruppe,
    Leistungsverzeichnis,
    Position,
)
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User


# The two totals the bug report names, reproduced exactly.
TOTAL_WALL = Decimal("1071.27")
TOTAL_CEILING = Decimal("489.05")
# The correct Leibung value the calculation engine produced before the
# sync clobbered it.
LEIBUNG_VALUE = Decimal("35.96")


async def _seed(db: AsyncSession) -> dict:
    """User → Project → Building → Floor → Unit → one active Room whose
    cached wall/ceiling areas equal the bug-report totals, plus an LV
    with one wall, one ceiling and one Leibung position."""
    user = User(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test User",
    )
    db.add(user)
    await db.flush()

    project = Project(id=uuid.uuid4(), user_id=user.id, name="Smoke-Test")
    db.add(project)
    await db.flush()

    building = Building(id=uuid.uuid4(), project_id=project.id, name="Haus 1")
    db.add(building)
    await db.flush()

    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db.add(floor)
    await db.flush()

    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top 1")
    db.add(unit)
    await db.flush()

    # One active room carrying the exact totals from the incident, so the
    # endpoint's Σ produces total_wall=1071.27 and total_ceiling=489.05.
    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Wohnung gesamt",
        area_m2=TOTAL_CEILING,          # ceiling fan-out = Σ area_m2
        perimeter_m=Decimal("100.0"),
        height_m=Decimal("2.5"),
        wall_area_net_m2=TOTAL_WALL,    # wall fan-out = Σ wall_area_net_m2
        is_active=True,
    )
    db.add(room)
    await db.flush()

    lv = Leistungsverzeichnis(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Malerarbeiten LV",
        trade="malerarbeiten",
    )
    db.add(lv)
    await db.flush()

    gruppe = Leistungsgruppe(
        id=uuid.uuid4(),
        lv_id=lv.id,
        nummer="01",
        bezeichnung="Malerarbeiten",
    )
    db.add(gruppe)
    await db.flush()

    # Three positions mirroring exactly what MalerarbeitenCalculator emits.
    pos_wall = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        positions_nummer="01.01",
        kurztext="Wandbeschichtung Dispersion weiß, 2× Anstrich auf vorbehandeltem Untergrund",
        einheit="m2",
        menge=Decimal("0"),
    )
    pos_ceiling = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        positions_nummer="02.01",
        kurztext="Deckenbeschichtung Dispersion weiß, 2× Anstrich auf vorbehandeltem Untergrund",
        einheit="m2",
        menge=Decimal("0"),
    )
    pos_leibung = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        positions_nummer="03.01",
        kurztext="Leibungsbeschichtung Dispersion weiß, 2× Anstrich",
        einheit="m2",
        menge=LEIBUNG_VALUE,   # correct calc-engine value, must survive
    )
    db.add_all([pos_wall, pos_ceiling, pos_leibung])
    await db.commit()

    return {
        "user": user,
        "lv_id": lv.id,
        "pos_wall_id": pos_wall.id,
        "pos_ceiling_id": pos_ceiling.id,
        "pos_leibung_id": pos_leibung.id,
    }


async def test_sync_wall_areas_preserves_leibung(db_session: AsyncSession):
    seed = await _seed(db_session)

    result = await sync_wall_areas(
        lv_id=seed["lv_id"], user=seed["user"], db=db_session
    )

    # --- The fix: Leibung kept its value, was not overwritten ---------
    leibung = await db_session.get(Position, seed["pos_leibung_id"])
    assert float(leibung.menge) == pytest.approx(35.96), (
        "Leibung darf NICHT die Wandfläche bekommen — Wert muss erhalten bleiben"
    )

    # --- Unchanged behaviour: real wall + ceiling matches still work ---
    wall = await db_session.get(Position, seed["pos_wall_id"])
    ceiling = await db_session.get(Position, seed["pos_ceiling_id"])
    assert float(wall.menge) == pytest.approx(1071.27)
    assert float(ceiling.menge) == pytest.approx(489.05)

    # --- Response counters ---------------------------------------------
    assert result["wall_positions_updated"] == 1
    assert result["ceiling_positions_updated"] == 1
    assert result["positions_skipped_leibung"] == 1
    assert result["total_wall_area_m2"] == 1071.27
    assert result["total_ceiling_area_m2"] == 489.05


async def test_leibung_classifier_helpers(db_session: AsyncSession):
    """Pin the classifier order: a Leibung row is leibung-only and is
    explicitly NOT a wall row; wall/ceiling rows are untouched."""

    def _pos(kurztext: str) -> Position:
        return Position(
            id=uuid.uuid4(),
            gruppe_id=uuid.uuid4(),
            positions_nummer="x",
            kurztext=kurztext,
            einheit="m2",
        )

    leibung = _pos("Leibungsbeschichtung Dispersion weiß, 2× Anstrich")
    wall = _pos("Wandbeschichtung Dispersion weiß, 2× Anstrich")
    ceiling = _pos("Deckenbeschichtung Dispersion weiß, 2× Anstrich")

    # Leibung: leibung-only, NOT wall, NOT ceiling.
    assert _is_leibung_position(leibung) is True
    assert _is_wall_position(leibung) is True  # text still matches "anstrich"…
    # …which is exactly why the loop must check _is_leibung_position FIRST.
    assert _is_ceiling_position(leibung) is False

    # Real wall + ceiling rows are unaffected and not seen as Leibung.
    assert _is_leibung_position(wall) is False
    assert _is_wall_position(wall) is True
    assert _is_leibung_position(ceiling) is False
    assert _is_ceiling_position(ceiling) is True
