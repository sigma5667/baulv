"""Tests for the v23.5 project-delete flow.

The DELETE endpoint itself has lived in the codebase since the
initial commit — what's new in v23.5 is the *frontend* surface
that finally calls it. These tests pin the behaviour the UI now
relies on:

  1. A successful delete removes the project row and cascades to
     every child entity (LV, Building, Plan, Room, etc.). The
     cascade is configured at the SQLAlchemy relationship layer;
     we verify it by counting child rows before/after.
  2. Cross-tenant protection: User B cannot delete User A's
     project. The endpoint raises 403.
  3. 404 for a non-existent project ID.

The cascade test is the important one — without it, deleting a
project would leave orphaned LVs in the DB (taking up space, and
potentially causing odd behaviour if the same UUID was ever
re-issued, which UUID4 collisions make practically impossible
but the test still locks the contract).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import delete_project
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User


async def _seed_project_with_children(
    db: AsyncSession,
    *,
    email_prefix: str = "u",
) -> tuple[User, Project, dict[str, uuid.UUID]]:
    """Seed a User → Project tree with one of every child entity.

    Returns ``(user, project, ids)`` where ``ids`` carries the UUIDs
    of every child row so the cascade-delete test can assert each
    one is gone after the parent is deleted.
    """
    user = User(
        id=uuid.uuid4(),
        email=f"{email_prefix}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db.add(user)
    await db.flush()

    project = Project(id=uuid.uuid4(), user_id=user.id, name="Cascade Test")
    db.add(project)
    await db.flush()

    # Building → Floor → Unit → Room
    building = Building(
        id=uuid.uuid4(), project_id=project.id, name="Haus 1"
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
    )
    db.add(room)
    await db.flush()

    # LV → Gruppe → Position
    lv = Leistungsverzeichnis(
        id=uuid.uuid4(),
        project_id=project.id,
        name="LV1",
        trade="malerarbeiten",
    )
    db.add(lv)
    await db.flush()

    gruppe = Leistungsgruppe(
        id=uuid.uuid4(), lv_id=lv.id, nummer="01", bezeichnung="LG1"
    )
    db.add(gruppe)
    await db.flush()

    position = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        positions_nummer="01.01",
        kurztext="Test",
        einheit="m2",
    )
    db.add(position)
    await db.commit()

    return user, project, {
        "building": building.id,
        "floor": floor.id,
        "unit": unit.id,
        "room": room.id,
        "lv": lv.id,
        "gruppe": gruppe.id,
        "position": position.id,
    }


# ---------------------------------------------------------------------------
# 1. Cascade delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_project_cascades_to_all_children(
    db_session: AsyncSession,
):
    """Deleting a project must remove every child row across the
    LV tree AND the Building tree. SQLAlchemy's ORM-level cascades
    (``cascade="all, delete-orphan"``) drive this — the test
    locks the contract so a future relationship refactor can't
    quietly drop ``delete-orphan`` and start leaking rows."""
    user, project, ids = await _seed_project_with_children(db_session)

    # Sanity: every entity is present pre-delete.
    for model, pk in (
        (Building, ids["building"]),
        (Floor, ids["floor"]),
        (Unit, ids["unit"]),
        (Room, ids["room"]),
        (Leistungsverzeichnis, ids["lv"]),
        (Leistungsgruppe, ids["gruppe"]),
        (Position, ids["position"]),
    ):
        assert (
            await db_session.get(model, pk)
        ) is not None, f"{model.__name__} {pk} missing pre-delete"

    await delete_project(
        project_id=project.id,
        user=user,
        db=db_session,
    )
    await db_session.commit()

    # Project itself is gone.
    assert await db_session.get(Project, project.id) is None

    # Every child is gone too.
    for model, pk in (
        (Building, ids["building"]),
        (Floor, ids["floor"]),
        (Unit, ids["unit"]),
        (Room, ids["room"]),
        (Leistungsverzeichnis, ids["lv"]),
        (Leistungsgruppe, ids["gruppe"]),
        (Position, ids["position"]),
    ):
        assert (
            await db_session.get(model, pk)
        ) is None, (
            f"{model.__name__} {pk} survived project delete — cascade broken"
        )


# ---------------------------------------------------------------------------
# 2. Cross-tenant protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_delete_user_as_project(
    db_session: AsyncSession,
):
    """User B authenticated, attempts to delete User A's project.
    Endpoint must 403 and the project must remain."""
    user_a, project, _ = await _seed_project_with_children(
        db_session, email_prefix="usera"
    )

    user_b = User(
        id=uuid.uuid4(),
        email=f"userb-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="User B",
    )
    db_session.add(user_b)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await delete_project(
            project_id=project.id,
            user=user_b,
            db=db_session,
        )
    assert exc_info.value.status_code == 403

    await db_session.rollback()
    # Project untouched.
    assert (
        await db_session.get(Project, project.id)
    ) is not None


# ---------------------------------------------------------------------------
# 3. 404 for missing project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_missing_project_404(db_session: AsyncSession):
    """Random UUID that isn't tied to any project → 404."""
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db_session.add(user)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await delete_project(
            project_id=uuid.uuid4(),
            user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 4. Other users' projects are untouched (defence in depth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_does_not_affect_other_projects(
    db_session: AsyncSession,
):
    """A second project owned by the *same* user must survive when
    the first is deleted — pin against an accidental ``DELETE`` that
    forgets the WHERE clause (the kind of bug a future bulk-delete
    refactor could re-introduce)."""
    user, project_one, _ = await _seed_project_with_children(db_session)

    # Second sibling project for the same user.
    project_two = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Sibling",
    )
    db_session.add(project_two)
    await db_session.commit()

    await delete_project(
        project_id=project_one.id,
        user=user,
        db=db_session,
    )
    await db_session.commit()

    # Sibling lives.
    assert (
        await db_session.get(Project, project_two.id)
    ) is not None
    # Original is gone.
    assert (
        await db_session.get(Project, project_one.id)
    ) is None
