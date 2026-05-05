"""Tests for the v23.7 defensive empty-rooms guard on /lv/{id}/calculate.

The calculation engine itself raises ``ValueError`` when a project
has zero rooms, and the API endpoint converts that to a 400. v23.7
added an explicit pre-flight check at the API layer so the failure
mode is observable without spinning up the engine — handy for ops
grep ("calculate.no_rooms") and a guard against future engine
refactors that might re-route the no-rooms path.

These tests pin two behaviours:

  1. Empty project → 400 with the German "bitte zuerst Räume
     hinzufügen…" message. Engine never gets called.
  2. Project with at least one room → the pre-flight passes and
     the call delegates to the engine. (We don't assert on the
     engine's output here; that's covered in
     ``test_calculation_engine``.)
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lv import run_calculation
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User


async def _seed_lv_without_rooms(
    db: AsyncSession,
) -> tuple[User, Leistungsverzeichnis]:
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Empty")
    db.add(project)
    await db.flush()
    lv = Leistungsverzeichnis(
        id=uuid.uuid4(),
        project_id=project.id,
        name="LV1",
        trade="malerarbeiten",
    )
    db.add(lv)
    await db.commit()
    return user, lv


async def _seed_lv_with_one_room(
    db: AsyncSession,
) -> tuple[User, Leistungsverzeichnis]:
    user, lv = await _seed_lv_without_rooms(db)

    building = Building(
        id=uuid.uuid4(), project_id=lv.project_id, name="Haus 1"
    )
    db.add(building)
    await db.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
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
        height_m=Decimal("2.7"),
    )
    db.add(room)
    await db.commit()
    return user, lv


# ---------------------------------------------------------------------------
# 1. Empty rooms → 400 with German message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calculate_empty_rooms_returns_400(db_session: AsyncSession):
    """Project with no rooms → 400 with the German pre-flight
    message. Engine must not be invoked (we patch it to assert)."""
    user, lv = await _seed_lv_without_rooms(db_session)

    with patch("app.api.lv.calculate_lv") as engine_spy:
        with pytest.raises(HTTPException) as exc_info:
            await run_calculation(lv_id=lv.id, user=user, db=db_session)

        # Pre-flight short-circuit ⇒ engine never called.
        engine_spy.assert_not_called()

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    # Pin the German wording so a refactor can't quietly change the
    # user-facing message — the frontend's special-case "no rooms"
    # branch keys off the substring.
    assert "Räume" in detail or "räume" in detail.lower()
    assert "Plan" in detail or "Bauplan" in detail


# ---------------------------------------------------------------------------
# 2. Non-empty project → pre-flight passes, engine is invoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calculate_with_rooms_delegates_to_engine(
    db_session: AsyncSession,
):
    """Pre-flight only blocks empty projects. With at least one room,
    the call is forwarded to ``calculate_lv``. We mock the engine
    so this test stays decoupled from the per-trade calculator
    fixtures (those are in ``test_calculation_engine``)."""
    user, lv = await _seed_lv_with_one_room(db_session)

    # Stub the engine to return an empty result list — the endpoint
    # only reads ``len()`` and a sum, so an empty list is the
    # cheapest valid stub.
    with patch("app.api.lv.calculate_lv") as engine_spy:
        engine_spy.return_value = []
        result = await run_calculation(
            lv_id=lv.id, user=user, db=db_session
        )

    engine_spy.assert_called_once()
    assert result["lv_id"] == str(lv.id)
    assert result["positions_calculated"] == 0
