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


# ---------------------------------------------------------------------------
# Helper for the v22.1 tests — same hierarchy boilerplate, fewer lines
# in the test bodies below.
# ---------------------------------------------------------------------------


async def _seed_unit(db: AsyncSession) -> tuple[User, Unit]:
    """Return a User and an Unit they own — minimum scaffolding for a
    POST /units/{unit_id}/rooms call."""
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
    await db.flush()
    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db.add(building)
    await db.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db.add(floor)
    await db.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="T")
    db.add(unit)
    await db.commit()
    return user, unit


# ---------------------------------------------------------------------------
# v22.1 — POST /rooms auto-estimate when only area is supplied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_room_with_only_area_estimates_perimeter(
    db_session: AsyncSession,
):
    """The whole point of v22.1: a manual room creation that has an
    area but no perimeter must not land at gross 0,00 m². The
    endpoint estimates 4·√A·1.10 and tags the source ``estimated``,
    so the wall-calc table renders a sensible number with the
    "geschätzt" hint instead of the red empty-state badge."""
    user, unit = await _seed_unit(db_session)

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Wohnzimmer", area_m2=20.0),
        user=user,
        db=db_session,
    )

    # 4 · √20 · 1.10 = 19.677… → 19.68 (full precision; see
    # test_perimeter_estimate.py for why this is 19.68 and not 19.67).
    assert response.perimeter_m == 19.68
    assert response.perimeter_source == "estimated"
    # Wall-area cache should also be filled — gross > 0 — because
    # the recalc step ran with the freshly-estimated perimeter.
    assert response.wall_area_gross_m2 is not None
    assert response.wall_area_gross_m2 > 0


@pytest.mark.asyncio
async def test_create_room_with_neither_perimeter_nor_area_keeps_null(
    db_session: AsyncSession,
):
    """User creates a room with just a name (no area, no perimeter).
    Both perimeter_m and perimeter_source must stay null so the UI
    can show the red 'Bitte eintragen' emergency-fallback. Estimating
    out of nothing would be lying."""
    user, unit = await _seed_unit(db_session)

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Abstellraum"),
        user=user,
        db=db_session,
    )

    assert response.perimeter_m is None
    assert response.perimeter_source is None


@pytest.mark.asyncio
async def test_create_room_with_explicit_perimeter_marks_manual(
    db_session: AsyncSession,
):
    """User typed both perimeter and area — the perimeter wins and is
    tagged ``manual``. We don't silently overwrite a typed perimeter
    with an estimate from the area, even if they'd disagree."""
    user, unit = await _seed_unit(db_session)

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Bad", area_m2=10.0, perimeter_m=14.5),
        user=user,
        db=db_session,
    )

    assert response.perimeter_m == 14.5
    assert response.perimeter_source == "manual"


# ---------------------------------------------------------------------------
# v22.1 — POST /rooms infers ceiling_height_source from height_m value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_room_without_height_marks_default(
    db_session: AsyncSession,
):
    """Empty form-submit (no height typed) → backend uses 2,50 m
    fallback and tags the source ``default``. This is the "honest"
    state for a freshly-added Abstellraum the user hasn't measured
    yet — the wall-calc table renders a subtle 'Standardwert'
    hint, not a manual badge."""
    user, unit = await _seed_unit(db_session)

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Stiegenhaus"),
        user=user,
        db=db_session,
    )

    assert response.ceiling_height_source == "default"


@pytest.mark.asyncio
async def test_create_room_with_explicit_2_50_height_marks_default(
    db_session: AsyncSession,
):
    """Defensive belt: even if a frontend regression starts pre-
    filling the height field with 2,50, the backend treats the
    Austrian residential standard fallback as ``default``. The
    user's intent is "use the standard"; tagging it ``manual``
    would be wrong."""
    user, unit = await _seed_unit(db_session)

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="WC", height_m=2.5),
        user=user,
        db=db_session,
    )

    assert response.ceiling_height_source == "default"


@pytest.mark.asyncio
async def test_create_room_with_explicit_other_height_marks_manual(
    db_session: AsyncSession,
):
    """User typed a real measurement (e.g. 2,80 m for an Altbau) →
    source ``manual``. Differentiation from the 2,50 default is the
    whole reason the source flag exists."""
    user, unit = await _seed_unit(db_session)

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Salon", height_m=2.80),
        user=user,
        db=db_session,
    )

    assert response.ceiling_height_source == "manual"


@pytest.mark.asyncio
async def test_create_room_explicit_source_overrides_inferred(
    db_session: AsyncSession,
):
    """Caller passing ``ceiling_height_source`` explicitly is
    honoured even when the heuristic would say something else.
    Lets the plan-analysis pipeline (which already labels its rooms
    as ``schnitt`` / ``grundriss``) use the same endpoint without
    losing those labels."""
    user, unit = await _seed_unit(db_session)

    response = await create_room(
        unit_id=unit.id,
        data=RoomCreate(
            name="Wohnzimmer",
            height_m=2.5,
            ceiling_height_source="grundriss",
        ),
        user=user,
        db=db_session,
    )

    assert response.ceiling_height_source == "grundriss"


# ---------------------------------------------------------------------------
# v22.1 — Auto-estimate also fires through PUT / bulk-calculate-walls,
# because the logic lives in ``_recalculate_walls_and_persist``
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalc_auto_estimates_when_perimeter_missing_but_area_present(
    db_session: AsyncSession,
):
    """A legacy or partially-filled room (perimeter null + area
    populated) must come out of the recalc step with a non-zero
    gross. Covers the bulk-calculate-walls path: a user clicking
    "Wandflächen berechnen" on a project that contains rooms the
    pipeline didn't fully fill should get sensible values across
    the board, not a sea of 0,00 m².
    """
    user, unit = await _seed_unit(db_session)
    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Lager",
        area_m2=Decimal("16.0"),
        perimeter_m=None,
        perimeter_source=None,
        height_m=Decimal("2.5"),
        ceiling_height_source="default",
    )
    db_session.add(room)
    await db_session.commit()

    # Force a recalc the way bulk-calculate-walls would.
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select

    from app.api.rooms import _recalculate_walls_and_persist

    stmt = (
        select(Room).where(Room.id == room.id).options(selectinload(Room.openings))
    )
    fresh = (await db_session.execute(stmt)).scalars().first()
    assert fresh is not None
    await _recalculate_walls_and_persist(fresh)
    await db_session.flush()

    # 4 · √16 · 1.10 = 17.60 exactly.
    assert float(fresh.perimeter_m) == 17.60
    assert fresh.perimeter_source == "estimated"
    assert fresh.wall_area_gross_m2 is not None
    assert float(fresh.wall_area_gross_m2) > 0


# ---------------------------------------------------------------------------
# v22.2 — height write-back + bulk-calc auto-fill across many rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalc_writes_default_height_back_to_room(
    db_session: AsyncSession,
):
    """Bug 1 from the v22.2 report: a row with ``height_m IS NULL``
    + ``ceiling_height_source = 'default'`` showed gross/net
    computed against 2,50 m (correct) but the Deckenhöhe column
    rendered as the red empty-state badge (wrong). Root cause was
    that ``_recalculate_walls_and_persist`` updated the cache and
    the source flag but never wrote the resolved height back to
    the DB — so the table read null and showed "Bitte eintragen".

    After this fix the recalc step persists ``calc.height_used_m``
    so the DB reflects what was used."""
    user, unit = await _seed_unit(db_session)
    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Gang",
        area_m2=Decimal("8.0"),
        perimeter_m=Decimal("12.0"),
        perimeter_source="vision",
        # The bug-trigger combination: height null + source default.
        height_m=None,
        ceiling_height_source="default",
    )
    db_session.add(room)
    await db_session.commit()

    from sqlalchemy.orm import selectinload
    from sqlalchemy import select

    from app.api.rooms import _recalculate_walls_and_persist

    stmt = (
        select(Room).where(Room.id == room.id).options(selectinload(Room.openings))
    )
    fresh = (await db_session.execute(stmt)).scalars().first()
    assert fresh is not None
    await _recalculate_walls_and_persist(fresh)
    await db_session.flush()

    # Height must now be the resolved 2,50 m the calculator used,
    # not the null we started with.
    assert fresh.height_m is not None
    assert float(fresh.height_m) == 2.5
    assert fresh.ceiling_height_source == "default"


@pytest.mark.asyncio
async def test_recalc_does_not_overwrite_explicit_height(
    db_session: AsyncSession,
):
    """The write-back guard must only fire when height was null —
    a user-typed 2,80 m must not be silently replaced by 2,50."""
    user, unit = await _seed_unit(db_session)
    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Salon",
        area_m2=Decimal("30.0"),
        perimeter_m=Decimal("22.0"),
        perimeter_source="manual",
        height_m=Decimal("2.80"),
        ceiling_height_source="manual",
    )
    db_session.add(room)
    await db_session.commit()

    from sqlalchemy.orm import selectinload
    from sqlalchemy import select

    from app.api.rooms import _recalculate_walls_and_persist

    stmt = (
        select(Room).where(Room.id == room.id).options(selectinload(Room.openings))
    )
    fresh = (await db_session.execute(stmt)).scalars().first()
    assert fresh is not None
    await _recalculate_walls_and_persist(fresh)
    await db_session.flush()

    assert float(fresh.height_m) == 2.80
    assert fresh.ceiling_height_source == "manual"


@pytest.mark.asyncio
async def test_bulk_calculate_walls_fills_missing_perimeters(
    db_session: AsyncSession,
):
    """Bug 2 from the v22.2 report: a project with rooms missing
    perimeter_m (typical for partial Vision extractions) should
    have all of them estimated and recalculated by a single
    "Wandflächen berechnen" click. Drives the actual endpoint to
    cover both the explicit pre-fill in the bulk handler and the
    redundant auto-estimate inside the recalc helper."""
    from app.api.rooms import bulk_calculate_walls

    user, unit = await _seed_unit(db_session)

    # Three rooms simulating Tobi's case: each has an area but no
    # perimeter, room names roughly mirror the real Beta-tester
    # report.
    sample_areas = {
        "Gang": Decimal("8.0"),
        "Keller 1": Decimal("16.0"),
        "Technikraum": Decimal("9.0"),
    }
    for name, area in sample_areas.items():
        db_session.add(
            Room(
                id=uuid.uuid4(),
                unit_id=unit.id,
                name=name,
                area_m2=area,
                perimeter_m=None,
                perimeter_source=None,
                height_m=None,
                ceiling_height_source="default",
            )
        )
    await db_session.commit()

    # Need the project_id for the endpoint — fetch it via the unit.
    from sqlalchemy import select

    from app.db.models.project import Floor as FloorModel

    floor = await db_session.get(FloorModel, unit.floor_id)
    assert floor is not None
    from app.db.models.project import Building as BuildingModel

    building = await db_session.get(BuildingModel, floor.building_id)
    assert building is not None
    project_id = building.project_id

    response = await bulk_calculate_walls(
        project_id=project_id, user=user, db=db_session
    )

    assert response.rooms_calculated == 3
    # Every room should have a non-zero gross now.
    for r in response.results:
        assert r.wall_area_gross_m2 > 0

    # And the DB rows should reflect estimated perimeters + filled
    # heights so the wall-calc table renders cleanly without
    # re-fetching.
    from app.db.models.project import Room as RoomModel

    rows = (
        await db_session.execute(
            select(RoomModel).where(RoomModel.unit_id == unit.id)
        )
    ).scalars().all()
    for row in rows:
        assert row.perimeter_m is not None
        assert row.perimeter_source == "estimated"
        assert row.height_m is not None
        assert float(row.height_m) == 2.5
