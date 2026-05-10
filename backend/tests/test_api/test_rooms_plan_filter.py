"""Tests for the v24.0 plan_id filter on GET /projects/{id}/rooms.

The endpoint used to always return every room of a project; v24.0
adds an optional ``plan_id`` query param to scope to one plan.
PlanAnalysisPage uses this for the "Plan-Filter"-dropdown and the
per-plan "Räume filtern"-button on each plan card.

Coverage:

  1. Without ``plan_id`` query param → all rooms (incl. those with
     ``plan_id IS NULL``, i.e. manually-created rooms).
  2. With ``plan_id=<plan-A>`` → only rooms from plan A; manual
     rooms and rooms from other plans excluded.
  3. With ``plan_id=<some-random-uuid>`` → empty list (no leak of
     other tenants' rooms, no 404 — the filter is opaque to
     non-matching plan_ids).
  4. Cross-tenant: User B requesting User A's project still gets
     the 403 from the existing ownership check, regardless of the
     plan_id query.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rooms import list_project_rooms
from app.db.models.plan import Plan
from app.db.models.project import (
    Building,
    Floor,
    Project,
    Room,
    Unit,
)
from app.db.models.user import User


async def _seed_user(db: AsyncSession, *, prefix: str = "u") -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{prefix}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_two_plan_project(
    db: AsyncSession, *, user: User
) -> tuple[Project, Plan, Plan, list[Room]]:
    """Seed a project with two plans and four rooms:

      - room A → plan A
      - room B → plan A
      - room C → plan B
      - room D → no plan (manually created)
    """
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Project")
    db.add(project)
    await db.flush()

    plan_a = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="grundriss_eg.pdf",
        file_path="/tmp/a.pdf",
    )
    plan_b = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="grundriss_og.pdf",
        file_path="/tmp/b.pdf",
    )
    db.add_all([plan_a, plan_b])
    await db.flush()

    # Building → Floor → Unit chain (rooms.unit_id is NOT NULL).
    building = Building(
        id=uuid.uuid4(), project_id=project.id, name="Haus", sort_order=0
    )
    db.add(building)
    await db.flush()
    floor = Floor(
        id=uuid.uuid4(), building_id=building.id, name="EG", level_number=0
    )
    db.add(floor)
    await db.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top 1")
    db.add(unit)
    await db.flush()

    rooms = [
        Room(
            id=uuid.uuid4(),
            unit_id=unit.id,
            plan_id=plan_a.id,
            name="Wohnzimmer",
            area_m2=Decimal("20.0"),
        ),
        Room(
            id=uuid.uuid4(),
            unit_id=unit.id,
            plan_id=plan_a.id,
            name="Küche",
            area_m2=Decimal("10.0"),
        ),
        Room(
            id=uuid.uuid4(),
            unit_id=unit.id,
            plan_id=plan_b.id,
            name="Schlafzimmer",
            area_m2=Decimal("15.0"),
        ),
        Room(
            id=uuid.uuid4(),
            unit_id=unit.id,
            plan_id=None,
            name="Manuell hinzugefügt",
            area_m2=Decimal("5.0"),
        ),
    ]
    for r in rooms:
        db.add(r)
    await db.commit()
    return project, plan_a, plan_b, rooms


# ---------------------------------------------------------------------------
# 1. No filter → all rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_filter_returns_all_rooms(db_session: AsyncSession):
    user = await _seed_user(db_session)
    project, _, _, _ = await _seed_two_plan_project(db_session, user=user)

    rooms = await list_project_rooms(
        project_id=project.id, plan_id=None, user=user, db=db_session
    )
    assert len(rooms) == 4
    names = {r.name for r in rooms}
    assert names == {
        "Wohnzimmer",
        "Küche",
        "Schlafzimmer",
        "Manuell hinzugefügt",
    }


# ---------------------------------------------------------------------------
# 2. Filter to plan A → only plan-A rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_to_plan_a_returns_only_plan_a_rooms(
    db_session: AsyncSession,
):
    user = await _seed_user(db_session)
    project, plan_a, _, _ = await _seed_two_plan_project(
        db_session, user=user
    )

    rooms = await list_project_rooms(
        project_id=project.id,
        plan_id=plan_a.id,
        user=user,
        db=db_session,
    )
    assert len(rooms) == 2
    names = {r.name for r in rooms}
    assert names == {"Wohnzimmer", "Küche"}
    # Defence: every returned room actually links back to plan A.
    assert all(r.plan_id == plan_a.id for r in rooms)


# ---------------------------------------------------------------------------
# 3. Filter to nonexistent plan_id → empty result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_to_unknown_plan_id_returns_empty(
    db_session: AsyncSession,
):
    user = await _seed_user(db_session)
    project, _, _, _ = await _seed_two_plan_project(db_session, user=user)

    rooms = await list_project_rooms(
        project_id=project.id,
        plan_id=uuid.uuid4(),  # random, not in DB
        user=user,
        db=db_session,
    )
    assert rooms == []


# ---------------------------------------------------------------------------
# 4. Cross-tenant: User B → 403 even with plan_id query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_returns_403(db_session: AsyncSession):
    user_a = await _seed_user(db_session, prefix="ua")
    project, plan_a, _, _ = await _seed_two_plan_project(
        db_session, user=user_a
    )

    user_b = await _seed_user(db_session, prefix="ub")

    with pytest.raises(HTTPException) as exc_info:
        await list_project_rooms(
            project_id=project.id,
            plan_id=plan_a.id,
            user=user_b,
            db=db_session,
        )
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# 5. Manual-only rooms excluded by any plan filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_room_excluded_when_filter_active(
    db_session: AsyncSession,
):
    """The manually-created room (plan_id=NULL) only appears when
    no filter is set. Locking this behaviour so that a future
    refactor that decides "include NULL plan_id rooms in every
    filter" doesn't pass silently — that would surprise users
    who clicked "Räume aus Plan X anzeigen"."""
    user = await _seed_user(db_session)
    project, plan_a, _, _ = await _seed_two_plan_project(
        db_session, user=user
    )

    rooms = await list_project_rooms(
        project_id=project.id,
        plan_id=plan_a.id,
        user=user,
        db=db_session,
    )
    names = {r.name for r in rooms}
    assert "Manuell hinzugefügt" not in names
