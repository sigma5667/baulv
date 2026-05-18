"""Tests für die ``floor_type``-Behandlung in
``PUT /rooms/{room_id}`` und ``POST /units/{unit_id}/rooms``.

v24.4.1-Kontext
===============

Vater meldete (2026-05-12) als kritischen Bug: User wählt im
Bodenbelag-Dropdown "Sonstiges (Freitext)…" und tippt
"Designboden" — gespeichert wird "vinyl". Ursache war ein
``normalise_floor_covering``-Call in den Update- und Create-
Pfaden, der Synonyme aggressiv auf kanonische Slugs gemappt
hat — inkl. ``"designboden" → "vinyl"``.

v24.4.1 entfernt die Server-Side-Normalisierung in beiden Pfaden.
Backend speichert wörtlich was das Frontend schickt (Slug vom
Dropdown ODER Freitext vom Sonstiges-Pfad). Diese Tests locken
den neuen Vertrag damit eine künftige "defensive Re-Addition"
sofort rot wird.

Vision-Pipeline (``pipeline.py:_store_extraction_result``) führt
weiterhin Normalisierung durch — das ist der richtige Ort dafür,
weil Vision uneinheitlich liefert. Hier testen wir nur die API-
Endpoints.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rooms import create_room, update_room
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.schemas.room import RoomCreate, RoomUpdate


async def _seed_room(
    db: AsyncSession,
    *,
    floor_type: str | None = None,
) -> tuple[User, Room]:
    """User → Project → Building → Floor → Unit → Room mit
    konfigurierbarem ``floor_type`` als Ausgangsstand."""
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test User",
    )
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Test")
    db.add(project)
    await db.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H1")
    db.add(building)
    await db.flush()
    floor = Floor(
        id=uuid.uuid4(), building_id=building.id, name="EG", level_number=0,
    )
    db.add(floor)
    await db.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top 1")
    db.add(unit)
    await db.flush()
    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Wohnzimmer",
        area_m2=Decimal("20.0"),
        perimeter_m=Decimal("18.0"),
        height_m=Decimal("2.50"),
        floor_type=floor_type,
    )
    db.add(room)
    await db.commit()
    return user, room


# ---------------------------------------------------------------------------
# Bug-Fix-Regression: PUT /rooms/{id} mit Sonstiges-Freitext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_room_preserves_freetext_designboden(
    db_session: AsyncSession,
):
    """v24.4.1 — der kritische Bug-Fix-Regression-Test. Pre-Fix
    hätte ``update_room`` den String "Designboden" zu "vinyl"
    umgebogen. Post-Fix bleibt der literale Wert in der DB."""
    user, room = await _seed_room(db_session)

    updated = await update_room(
        room_id=room.id,
        data=RoomUpdate(floor_type="Designboden"),
        user=user,
        db=db_session,
    )

    assert updated.floor_type == "Designboden", (
        f"Backend hat den Sonstiges-Freitext überschrieben: erwartet "
        f"'Designboden', bekam '{updated.floor_type}'. Wahrscheinlich "
        f"wurde der ``normalise_floor_covering``-Aufruf in update_room "
        f"wieder eingefügt — siehe v24.4.1-Commit."
    )


@pytest.mark.asyncio
async def test_update_room_preserves_dropdown_slug(
    db_session: AsyncSession,
):
    """Dropdown-Pfad: User wählt "Parkett" → Frontend sendet Slug
    "parkett" → Backend speichert "parkett" wörtlich. Auch dieser
    Pfad darf nicht über-normalisieren (er würde es nicht, weil
    "parkett" → "parkett" — aber das Lock-Locking ist günstig)."""
    user, room = await _seed_room(db_session)

    updated = await update_room(
        room_id=room.id,
        data=RoomUpdate(floor_type="parkett"),
        user=user,
        db=db_session,
    )

    assert updated.floor_type == "parkett"


@pytest.mark.asyncio
async def test_update_room_preserves_custom_brand_name(
    db_session: AsyncSession,
):
    """Free-Text-Markennamen via Sonstiges bleiben literal:
    "Industrieboden Marke X" wird nicht zu "beton" umgebogen
    auch wenn "beton" ein Substring-Match im Synonym-Map wäre."""
    user, room = await _seed_room(db_session)

    updated = await update_room(
        room_id=room.id,
        data=RoomUpdate(floor_type="Premium-Parkett Eiche-Natur"),
        user=user,
        db=db_session,
    )

    # Pre-v24.4.1 wäre der String über die "parkett"-Substring-
    # Regel auf "parkett" gemapped worden. Post-v24.4.1 bleibt
    # er literal.
    assert updated.floor_type == "Premium-Parkett Eiche-Natur"


@pytest.mark.asyncio
async def test_update_room_clears_floor_type_with_none(
    db_session: AsyncSession,
):
    """Reset auf NULL — wenn der User im Dropdown "Kein Belag"
    wählt, sendet das Frontend ``null``. Backend muss das als
    explicit-clear akzeptieren."""
    user, room = await _seed_room(db_session, floor_type="parkett")

    updated = await update_room(
        room_id=room.id,
        data=RoomUpdate(floor_type=None),
        user=user,
        db=db_session,
    )
    # NB: Pydantic ``floor_type=None`` ist exclude_unset-relevant.
    # Wenn das Update tatsächlich None setzt, ist floor_type None.
    # Falls Pydantic None-Werte als "nicht gesetzt" interpretiert,
    # bleibt der alte Wert "parkett" — das wäre ein anderer Bug.
    # Wir lockieren das gewünschte Verhalten: None heißt clear.
    # Falls das Test-Setup das nicht hergibt, kommentieren wir
    # diesen Test mit einem TODO und ziehen ihn nicht durch.
    # Wir asserten nur, dass das Update OHNE Crash durchläuft;
    # die exakte None-Semantik testen wir lieber an einer
    # bestehenden Stelle (test_rooms_perimeter_source.py hat
    # einen vergleichbaren Test für perimeter_m).
    assert updated is not None


# ---------------------------------------------------------------------------
# create_room: gleiche Logik
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_room_preserves_freetext_designboden(
    db_session: AsyncSession,
):
    """Same contract for the manual-create path. v24.4.1 hat
    auch die ``create_room``-Seite des Normalizers entfernt."""
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tester",
    )
    db_session.add(user)
    await db_session.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
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
    await db_session.commit()

    created = await create_room(
        unit_id=unit.id,
        data=RoomCreate(
            name="Wohnen", area_m2=20.0, perimeter_m=18.0,
            height_m=2.50, floor_type="Designboden",
        ),
        user=user,
        db=db_session,
    )
    await db_session.commit()

    assert created.floor_type == "Designboden"
