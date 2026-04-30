"""Tests for the ``perimeter_source`` provenance promotion in
``PUT /rooms/{room_id}`` (and the parallel logic in
``POST /units/{unit_id}/rooms``).

The contract: any time the user supplies a perimeter via the API,
the source is promoted to ``manual`` so the wall-calc table stops
flagging the value as an estimate. Clearing the perimeter (sending
``null``) must also clear the source — otherwise a row could show
``perimeter_source='manual'`` with no ``perimeter_m`` and the empty-
state fallback wouldn't kick in.

We exercise the handler functions directly. The auth/ownership
plumbing is tested elsewhere; here we want to lock the
provenance-promotion logic before some future refactor "simplifies"
it away.
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
    perimeter_m: Decimal | None = Decimal("18.0"),
    perimeter_source: str | None = "vision",
) -> tuple[User, Room]:
    """Create the smallest hierarchy we need for a Room update test.

    User → Project → Building → Floor → Unit → Room. The Room is
    seeded with a known perimeter + source so the tests can prove
    the field is *changed* by the endpoint, not coincidentally
    equal to the post-condition.
    """
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

    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db.add(floor)
    await db.flush()

    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top")
    db.add(unit)
    await db.flush()

    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Wohnzimmer",
        area_m2=Decimal("20.0"),
        perimeter_m=perimeter_m,
        perimeter_source=perimeter_source,
        height_m=Decimal("2.5"),
        ceiling_height_source="grundriss",
    )
    db.add(room)
    await db.commit()
    return user, room


# ---------------------------------------------------------------------------
# update_room — the primary surface for inline edits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_room_promotes_perimeter_source_to_manual(
    db_session: AsyncSession,
):
    """User edits the perimeter via the inline editor → source must
    flip from ``vision`` to ``manual`` so the wall-calc table stops
    flagging the value as an estimate."""
    user, room = await _seed_room(
        db_session, perimeter_m=Decimal("18.0"), perimeter_source="vision"
    )

    await update_room(
        room_id=room.id,
        data=RoomUpdate(perimeter_m=22.5),
        user=user,
        db=db_session,
    )

    refreshed = await db_session.get(Room, room.id)
    assert refreshed is not None
    assert float(refreshed.perimeter_m) == 22.5
    assert refreshed.perimeter_source == "manual"


@pytest.mark.asyncio
async def test_update_room_promotes_estimated_to_manual(
    db_session: AsyncSession,
):
    """A pipeline-estimated perimeter is the most common case for a
    user inline-edit. Confirm the same promotion happens — otherwise
    the row would stay tagged ``estimated`` after the user explicitly
    typed in their own value, which is the exact UX bug this whole
    track is meant to prevent."""
    user, room = await _seed_room(
        db_session,
        perimeter_m=Decimal("19.67"),
        perimeter_source="estimated",
    )

    await update_room(
        room_id=room.id,
        data=RoomUpdate(perimeter_m=20.0),
        user=user,
        db=db_session,
    )

    refreshed = await db_session.get(Room, room.id)
    assert refreshed is not None
    assert refreshed.perimeter_source == "manual"


@pytest.mark.asyncio
async def test_update_room_clears_source_when_perimeter_set_to_null(
    db_session: AsyncSession,
):
    """Sending ``perimeter_m=null`` is the user clearing the value.
    The source must also clear so the table can render the empty-
    state badge — leaving ``perimeter_source='manual'`` next to a
    null perimeter would be inconsistent state."""
    user, room = await _seed_room(
        db_session, perimeter_m=Decimal("18.0"), perimeter_source="vision"
    )

    await update_room(
        room_id=room.id,
        data=RoomUpdate(perimeter_m=None),
        user=user,
        db=db_session,
    )

    refreshed = await db_session.get(Room, room.id)
    assert refreshed is not None
    assert refreshed.perimeter_m is None
    assert refreshed.perimeter_source is None


@pytest.mark.asyncio
async def test_update_room_keeps_explicit_source_value(
    db_session: AsyncSession,
):
    """If the caller passes both ``perimeter_m`` AND an explicit
    ``perimeter_source``, we honour the explicit value — don't
    silently overwrite it with ``manual``."""
    user, room = await _seed_room(db_session)

    await update_room(
        room_id=room.id,
        data=RoomUpdate(perimeter_m=15.0, perimeter_source="vision"),
        user=user,
        db=db_session,
    )

    refreshed = await db_session.get(Room, room.id)
    assert refreshed is not None
    assert refreshed.perimeter_source == "vision"


@pytest.mark.asyncio
async def test_update_room_other_field_does_not_touch_perimeter_source(
    db_session: AsyncSession,
):
    """Editing an unrelated field (room name, room type) must leave
    the perimeter provenance flag alone — otherwise a rename would
    silently re-tag an estimated room as manual."""
    user, room = await _seed_room(
        db_session, perimeter_m=Decimal("19.67"), perimeter_source="estimated"
    )

    await update_room(
        room_id=room.id,
        data=RoomUpdate(name="Schlafzimmer"),
        user=user,
        db=db_session,
    )

    refreshed = await db_session.get(Room, room.id)
    assert refreshed is not None
    assert refreshed.name == "Schlafzimmer"
    assert refreshed.perimeter_source == "estimated"


# ---------------------------------------------------------------------------
# create_room — the parallel logic on POST /units/{unit_id}/rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_room_with_perimeter_marks_manual_source(
    db_session: AsyncSession,
):
    """Manual-room creation through the UI provides a perimeter; the
    endpoint should set ``perimeter_source='manual'`` so the wall-
    calc table doesn't show the row as an estimate."""
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db_session.add(user)
    await db_session.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db_session.add(project)
    await db_session.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="T")
    db_session.add(unit)
    await db_session.commit()

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Bad", perimeter_m=12.5, height_m=2.5),
        user=user,
        db=db_session,
    )

    assert response.perimeter_source == "manual"


@pytest.mark.asyncio
async def test_create_room_without_perimeter_leaves_source_null(
    db_session: AsyncSession,
):
    """If the user creates a room without entering a perimeter, the
    source must stay null so the table renders the empty-state badge.
    Setting it to ``manual`` here would be a lie — the user typed
    nothing."""
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db_session.add(user)
    await db_session.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db_session.add(project)
    await db_session.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db_session.add(building)
    await db_session.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="T")
    db_session.add(unit)
    await db_session.commit()

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Abstellraum"),
        user=user,
        db=db_session,
    )

    assert response.perimeter_m is None
    assert response.perimeter_source is None
