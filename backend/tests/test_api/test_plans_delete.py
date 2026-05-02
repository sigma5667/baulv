"""Tests for the plan-deletion endpoint added in v23.

Four behaviours we want to lock:

1. ``delete_rooms=False`` removes the Plan row but leaves rooms
   intact (their plan_id link is broken via FK SET NULL — verified
   on Postgres in production; in SQLite tests we just confirm the
   rooms survive the delete).
2. ``delete_rooms=True`` removes the Plan AND every Room with
   ``plan_id == this_plan``, including their openings and
   Berechnungsnachweise via cascade.
3. Cross-tenant protection — User B cannot delete User A's plan.
4. The ``plan.deleted`` audit log entry is written with accurate
   counts (rooms_deleted, openings_deleted, proofs_deleted).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.plans import delete_plan, plan_deletion_preview
from app.db.models.audit import AuditLogEntry
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.plan import Plan
from app.db.models.project import (
    Building,
    Floor,
    Opening,
    Project,
    Room,
    Unit,
)
from app.db.models.user import User


# ---------------------------------------------------------------------------
# Helpers — small enough that each test reads top-down without
# bouncing into a 30-line fixture.
# ---------------------------------------------------------------------------


def _mock_request() -> MagicMock:
    """Stand-in for the FastAPI Request the audit logger reads.

    Only the headers and ``client`` attribute matter here; we return
    minimal defaults so ``audit.log_event`` runs without raising.
    """
    request = MagicMock()
    request.headers = {}
    request.client = None
    return request


async def _seed_user_with_project(
    db: AsyncSession,
    *,
    email_prefix: str = "u",
) -> tuple[User, Project]:
    user = User(
        id=uuid.uuid4(),
        email=f"{email_prefix}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db.add(project)
    await db.flush()
    return user, project


async def _seed_plan_with_rooms(
    db: AsyncSession,
    *,
    project: Project,
    n_rooms: int,
    on_disk_file: bool = False,
) -> tuple[Plan, list[Room]]:
    """Seed one plan + ``n_rooms`` rooms linked to it via plan_id.

    Optionally drops a tempfile on disk so the file-unlink path can
    be exercised.
    """
    if on_disk_file:
        fd, path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(b"%PDF-1.4 dummy")
    else:
        path = f"/nonexistent/{uuid.uuid4()}.pdf"

    plan = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="test.pdf",
        file_path=path,
    )
    db.add(plan)
    await db.flush()

    building = Building(id=uuid.uuid4(), project_id=project.id, name="H")
    db.add(building)
    await db.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db.add(floor)
    await db.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="T")
    db.add(unit)
    await db.flush()

    rooms: list[Room] = []
    for i in range(n_rooms):
        room = Room(
            id=uuid.uuid4(),
            unit_id=unit.id,
            plan_id=plan.id,
            name=f"Raum {i + 1}",
            area_m2=Decimal("20.0"),
            perimeter_m=Decimal("18.0"),
            height_m=Decimal("2.5"),
            source="ai",
        )
        db.add(room)
        rooms.append(room)
    await db.commit()
    return plan, rooms


# ---------------------------------------------------------------------------
# delete_rooms=False — rooms survive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_plan_keeps_rooms(db_session: AsyncSession):
    """``delete_rooms=False`` removes the plan row only. The rooms
    that originated from it survive — in production via the FK
    SET NULL constraint clearing their plan_id; in this SQLite test
    we just verify the rooms still exist (FK enforcement is off by
    default on SQLite, so plan_id stays as a stale UUID, but the
    contract we care about — "rooms survive a kept-rooms delete" —
    is observable either way)."""
    user, project = await _seed_user_with_project(db_session)
    plan, rooms = await _seed_plan_with_rooms(db_session, project=project, n_rooms=3)

    result = await delete_plan(
        plan_id=plan.id,
        request=_mock_request(),
        delete_rooms=False,
        user=user,
        db=db_session,
    )

    assert result.delete_rooms is False
    assert result.rooms_deleted == 0
    assert result.openings_deleted == 0
    assert result.proofs_deleted == 0

    # Plan row gone.
    fresh_plan = await db_session.get(Plan, plan.id)
    assert fresh_plan is None

    # Rooms still present.
    surviving = (
        await db_session.execute(
            select(Room).where(Room.id.in_([r.id for r in rooms]))
        )
    ).scalars().all()
    assert len(surviving) == 3


# ---------------------------------------------------------------------------
# delete_rooms=True — rooms (+ openings + proofs) cascade away
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_plan_cascades_rooms_with_proofs(
    db_session: AsyncSession,
):
    """Full destructive path: the plan, the rooms it spawned, every
    Opening on those rooms, and every Berechnungsnachweis pointing
    to them all go away in one transaction. Counts in the
    ``PlanDeletionResult`` payload reflect what flushed."""
    user, project = await _seed_user_with_project(db_session)
    plan, rooms = await _seed_plan_with_rooms(db_session, project=project, n_rooms=2)

    # Add openings to each room.
    for room in rooms:
        db_session.add(
            Opening(
                id=uuid.uuid4(),
                room_id=room.id,
                opening_type="fenster",
                width_m=1.2,
                height_m=1.5,
                count=1,
                source="ai",
            )
        )

    # Build a tiny LV with one position so we can attach a
    # Berechnungsnachweis pointing to one of the rooms.
    lv = Leistungsverzeichnis(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Maler",
        trade="malerarbeiten",
    )
    db_session.add(lv)
    await db_session.flush()
    gruppe = Leistungsgruppe(
        id=uuid.uuid4(),
        lv_id=lv.id,
        name="Wand",
        gruppen_nummer="01",
    )
    db_session.add(gruppe)
    await db_session.flush()
    position = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        position_nummer="01.01",
        kurztext="Wand streichen",
        einheit="m²",
        menge=Decimal("50.0"),
    )
    db_session.add(position)
    await db_session.flush()
    db_session.add(
        Berechnungsnachweis(
            id=uuid.uuid4(),
            position_id=position.id,
            room_id=rooms[0].id,
            raw_quantity=Decimal("50.0"),
            formula_description="Test",
            formula_expression="20 * 2.5 * 1.0",
            net_quantity=Decimal("50.0"),
            unit="m²",
        )
    )
    await db_session.commit()

    result = await delete_plan(
        plan_id=plan.id,
        request=_mock_request(),
        delete_rooms=True,
        user=user,
        db=db_session,
    )

    assert result.delete_rooms is True
    assert result.rooms_deleted == 2
    assert result.openings_deleted == 2
    assert result.proofs_deleted == 1

    # Everything below the plan is gone.
    assert await db_session.get(Plan, plan.id) is None
    for room in rooms:
        assert await db_session.get(Room, room.id) is None

    # Position itself survives — only the proof goes.
    assert await db_session.get(Position, position.id) is not None
    proofs_remaining = (
        await db_session.execute(
            select(Berechnungsnachweis).where(
                Berechnungsnachweis.position_id == position.id
            )
        )
    ).scalars().all()
    assert proofs_remaining == []


# ---------------------------------------------------------------------------
# Manual rooms (plan_id IS NULL) are never touched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_plan_does_not_touch_manual_rooms(
    db_session: AsyncSession,
):
    """A manual room — created via ``POST /units/{id}/rooms`` and
    therefore with ``plan_id = NULL`` — must not be deleted just
    because it lives in the same project as a soon-to-be-deleted
    plan. The plan_id filter is the firewall."""
    user, project = await _seed_user_with_project(db_session)
    plan, _ = await _seed_plan_with_rooms(db_session, project=project, n_rooms=1)

    # A manual room in the same project, plan_id NULL.
    building = (
        await db_session.execute(
            select(Building).where(Building.project_id == project.id)
        )
    ).scalars().first()
    floor = (
        await db_session.execute(
            select(Floor).where(Floor.building_id == building.id)
        )
    ).scalars().first()
    unit = (
        await db_session.execute(
            select(Unit).where(Unit.floor_id == floor.id)
        )
    ).scalars().first()
    manual_room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        plan_id=None,
        name="Manuell",
        area_m2=Decimal("10.0"),
        source="manual",
    )
    db_session.add(manual_room)
    await db_session.commit()

    await delete_plan(
        plan_id=plan.id,
        request=_mock_request(),
        delete_rooms=True,
        user=user,
        db=db_session,
    )

    # The manual room must still be there.
    survived = await db_session.get(Room, manual_room.id)
    assert survived is not None


# ---------------------------------------------------------------------------
# Cross-tenant protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_plan_rejects_cross_tenant(db_session: AsyncSession):
    """User B trying to delete User A's plan must get a 404 / 403
    via ``verify_plan_owner``. The endpoint never even sees the
    delete-cascade path."""
    user_a, project_a = await _seed_user_with_project(db_session, email_prefix="a")
    plan_a, _ = await _seed_plan_with_rooms(
        db_session, project=project_a, n_rooms=1
    )

    user_b, _ = await _seed_user_with_project(db_session, email_prefix="b")

    with pytest.raises(HTTPException) as exc_info:
        await delete_plan(
            plan_id=plan_a.id,
            request=_mock_request(),
            delete_rooms=False,
            user=user_b,
            db=db_session,
        )
    assert exc_info.value.status_code in (403, 404)

    # Plan must still be there.
    assert await db_session.get(Plan, plan_a.id) is not None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_plan_writes_audit_entry(db_session: AsyncSession):
    """The ``plan.deleted`` event must land with the user_id and
    accurate counts in ``meta`` so the audit-log viewer can show
    "Maria hat plan-x.pdf gelöscht (3 Räume mitgelöscht)" later."""
    user, project = await _seed_user_with_project(db_session)
    plan, _ = await _seed_plan_with_rooms(db_session, project=project, n_rooms=2)

    await delete_plan(
        plan_id=plan.id,
        request=_mock_request(),
        delete_rooms=True,
        user=user,
        db=db_session,
    )

    rows = (
        await db_session.execute(
            select(AuditLogEntry)
            .where(AuditLogEntry.user_id == user.id)
            .where(AuditLogEntry.event_type == "plan.deleted")
        )
    ).scalars().all()
    assert len(rows) == 1
    entry = rows[0]
    meta = entry.meta or {}
    assert meta.get("filename") == "test.pdf"
    assert meta.get("delete_rooms") is True
    assert meta.get("rooms_deleted") == 2


# ---------------------------------------------------------------------------
# File unlink — best-effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_plan_unlinks_file_when_present(
    db_session: AsyncSession,
):
    """An on-disk PDF exists → the endpoint deletes it and reports
    ``file_unlinked=True``."""
    user, project = await _seed_user_with_project(db_session)
    plan, _ = await _seed_plan_with_rooms(
        db_session, project=project, n_rooms=0, on_disk_file=True
    )
    assert Path(plan.file_path).exists()

    result = await delete_plan(
        plan_id=plan.id,
        request=_mock_request(),
        delete_rooms=False,
        user=user,
        db=db_session,
    )
    assert result.file_unlinked is True
    assert not Path(plan.file_path).exists()


@pytest.mark.asyncio
async def test_delete_plan_handles_missing_file_gracefully(
    db_session: AsyncSession,
):
    """A non-existent file_path (e.g. an old plan whose file was
    cleaned up out of band) does not crash the delete — the DB row
    still goes away, ``file_unlinked`` is True (we count "already
    gone" as success so we don't false-flag a leak)."""
    user, project = await _seed_user_with_project(db_session)
    plan, _ = await _seed_plan_with_rooms(
        db_session, project=project, n_rooms=0, on_disk_file=False
    )

    result = await delete_plan(
        plan_id=plan.id,
        request=_mock_request(),
        delete_rooms=False,
        user=user,
        db=db_session,
    )
    assert result.file_unlinked is True
    assert await db_session.get(Plan, plan.id) is None


# ---------------------------------------------------------------------------
# Preview endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deletion_preview_counts_match_actual_delete(
    db_session: AsyncSession,
):
    """The preview returns the same counts the actual delete would
    report — that's how the dialog stays honest about its
    consequences. We hit the preview, then the delete with
    ``delete_rooms=True``, and assert the numbers line up."""
    user, project = await _seed_user_with_project(db_session)
    plan, rooms = await _seed_plan_with_rooms(db_session, project=project, n_rooms=4)
    for room in rooms:
        db_session.add(
            Opening(
                id=uuid.uuid4(),
                room_id=room.id,
                opening_type="fenster",
                width_m=1.0,
                height_m=1.5,
                count=1,
                source="ai",
            )
        )
    await db_session.commit()

    preview = await plan_deletion_preview(
        plan_id=plan.id, user=user, db=db_session
    )
    assert preview.rooms_linked == 4
    assert preview.openings_linked == 4
    assert preview.proofs_linked == 0

    result = await delete_plan(
        plan_id=plan.id,
        request=_mock_request(),
        delete_rooms=True,
        user=user,
        db=db_session,
    )
    assert result.rooms_deleted == preview.rooms_linked
    assert result.openings_deleted == preview.openings_linked
    assert result.proofs_deleted == preview.proofs_linked
