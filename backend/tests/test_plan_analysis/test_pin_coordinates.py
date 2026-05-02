"""Tests for the v23.1 plan-pin coordinate extraction.

Three behaviours we lock down:

1. ``_coerce_positive_int`` — the gatekeeper that turns Vision's
   loose "could be a positive int, could be 'n/a', could be -1"
   into either a positive int or None.
2. The pipeline's ``_store_extraction_result`` persists Vision-
   supplied coordinates onto the Room row, with the pipeline-owned
   page_number always overriding any Vision claim.
3. The API serialiser includes the new fields so the frontend (or
   the MCP tools) can read them back.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.plan import Plan
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.plan_analysis.pipeline import (
    _coerce_positive_int,
    _store_extraction_result,
)


# ---------------------------------------------------------------------------
# _coerce_positive_int — pure-function tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, 1),
        (1450, 1450),
        ("1450", 1450),  # string-int still coerces (Vision sometimes quotes)
        (1450.7, 1450),  # float gets truncated by ``int()``
    ],
)
def test_coerce_accepts_positive_values(value, expected):
    assert _coerce_positive_int(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,           # zero is the most common "I don't know" fallback
        -1,
        -5000,
        "n/a",       # Vision occasionally hallucinates
        "?",
        "",
        "abc",
        [1, 2],      # list — not a coordinate
    ],
)
def test_coerce_rejects_invalid_values(value):
    assert _coerce_positive_int(value) is None


# ---------------------------------------------------------------------------
# Pipeline persistence
# ---------------------------------------------------------------------------


async def _seed_plan(db: AsyncSession) -> tuple[User, Plan]:
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
    plan = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="t.pdf",
        file_path="/tmp/t.pdf",
    )
    db.add(plan)
    await db.commit()
    return user, plan


@pytest.mark.asyncio
async def test_pipeline_persists_full_coordinate_set(
    db_session: AsyncSession,
):
    """Vision returned all four coordinate fields plus the room
    name → all five v23.1 columns land on the Room row, including
    the pipeline-supplied page_number."""
    _, plan = await _seed_plan(db_session)

    result = {
        "floor_name": "EG",
        "floor_level": 0,
        "units": [
            {
                "unit_name": "W1",
                "unit_type": "wohnung",
                "rooms": [
                    {
                        "room_name": "WOHNEN / KOCHEN",
                        "area_m2": 32.84,
                        "perimeter_m": 24.32,
                        "perimeter_source": "labeled",
                        "position_x": 1450,
                        "position_y": 820,
                        "bbox_width": 540,
                        "bbox_height": 380,
                    }
                ],
            }
        ],
    }

    await _store_extraction_result(result, plan, db_session, page_number=2)
    await db_session.commit()

    rooms = (await db_session.execute(
        Room.__table__.select().where(Room.plan_id == plan.id)
    )).all()
    assert len(rooms) == 1
    row = rooms[0]
    assert row.position_x == 1450
    assert row.position_y == 820
    assert row.bbox_width == 540
    assert row.bbox_height == 380
    assert row.page_number == 2


@pytest.mark.asyncio
async def test_pipeline_drops_invalid_coords_keeps_page_number(
    db_session: AsyncSession,
):
    """Vision sent zero / negative / bogus values for the four
    coordinate fields → all four collapse to NULL. ``page_number``
    still gets set because the pipeline owns it; we don't lose
    page provenance just because the coordinates were unusable."""
    _, plan = await _seed_plan(db_session)

    result = {
        "floor_name": "EG",
        "floor_level": 0,
        "units": [
            {
                "unit_name": "W1",
                "unit_type": "wohnung",
                "rooms": [
                    {
                        "room_name": "BAD",
                        "area_m2": 4.30,
                        "position_x": -5,
                        "position_y": 0,
                        "bbox_width": "n/a",
                        "bbox_height": None,
                    }
                ],
            }
        ],
    }

    await _store_extraction_result(result, plan, db_session, page_number=1)
    await db_session.commit()

    rooms = (await db_session.execute(
        Room.__table__.select().where(Room.plan_id == plan.id)
    )).all()
    assert len(rooms) == 1
    row = rooms[0]
    assert row.position_x is None
    assert row.position_y is None
    assert row.bbox_width is None
    assert row.bbox_height is None
    # But page_number is still trustworthy — the pipeline injected it.
    assert row.page_number == 1


@pytest.mark.asyncio
async def test_pipeline_ignores_vision_supplied_page_number(
    db_session: AsyncSession,
):
    """Even if Vision claims its own ``page_number`` in the JSON,
    the pipeline overwrites it — Vision can't actually see which
    page index it's processing, so its claim is at best a guess
    and at worst hallucinated."""
    _, plan = await _seed_plan(db_session)

    result = {
        "floor_name": "EG",
        "floor_level": 0,
        "units": [
            {
                "unit_name": "W1",
                "unit_type": "wohnung",
                "rooms": [
                    {
                        "room_name": "DIELE",
                        "area_m2": 3.65,
                        "page_number": 99,  # Vision's lie — must be ignored
                        "position_x": 200,
                        "position_y": 300,
                    }
                ],
            }
        ],
    }

    await _store_extraction_result(result, plan, db_session, page_number=3)
    await db_session.commit()

    rooms = (await db_session.execute(
        Room.__table__.select().where(Room.plan_id == plan.id)
    )).all()
    assert len(rooms) == 1
    assert rooms[0].page_number == 3
    # Coordinates that WERE valid still got persisted.
    assert rooms[0].position_x == 200
    assert rooms[0].position_y == 300


# ---------------------------------------------------------------------------
# API serialisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_returns_coordinate_fields_in_room_response(
    db_session: AsyncSession,
):
    """The Pydantic ``RoomResponse`` schema must include the new
    coordinate fields so the frontend Room type stays in lock-step
    with what the API serves."""
    from app.api.rooms import get_room

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
    await db_session.flush()
    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Wohnen",
        area_m2=Decimal("20"),
        position_x=1450,
        position_y=820,
        page_number=1,
        bbox_width=540,
        bbox_height=380,
    )
    db_session.add(room)
    await db_session.commit()

    response = await get_room(room_id=room.id, user=user, db=db_session)
    # ``RoomResponse`` is a Pydantic model — fields are accessible
    # both as attributes (model instance) and via .model_dump().
    assert response.position_x == 1450
    assert response.position_y == 820
    assert response.page_number == 1
    assert response.bbox_width == 540
    assert response.bbox_height == 380
