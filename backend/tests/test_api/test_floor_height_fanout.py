"""Tests for the v24.6 Geschoss-Höhe fan-out (Option A).

The contract
============

``Floor.floor_height_m`` (the "Raumhöhe (m)" field on the Stockwerk
form) is the *default* ceiling height for rooms on that floor —
resolved ONLY through deliberate actions (Option A, "nur bewusste
Aktionen", decided 2026-07-03):

  * a room is CREATED (manual create or pipeline extraction),
  * the Geschoss-Höhe itself is CHANGED or CLEARED via
    ``PUT /floors/{id}`` — and only when the value really differs
    (the floor form echoes the pre-filled height on every save),
  * a room height is explicitly CLEARED (``height_m=null``).

Everything else — renames and other inline edits, opening CRUD, the
recompute buttons — recalculates but never re-sources a room. That
keeps Bestandsprojekte untouched: dormant ``floor_height_m`` values
from the pre-v24.6 era (Quick-Add seeds, the form's 2,50 pre-fill)
stay inert until the user deliberately re-saves a Geschoss-Höhe.

Priority is strict and must stay watertight:

  * ``manual`` / ``schnitt`` / ``grundriss`` — a real measurement.
    NEVER overwritten by the Geschoss-Höhe.
  * ``default`` / ``floor`` — a placeholder. Follows the
    Geschoss-Höhe on the deliberate actions above; falls back to the
    2,50 m default when the Geschoss-Höhe is cleared.

Product decision (2026-07-02): clearing a manual height via
``PUT /rooms/{id}`` with ``height_m=null`` falls back to the
Geschoss-Höhe (source ``floor``) — not to the bare 2,50 default —
because the Stockwerk-Vorgabe is the better default.

Two traps this suite deliberately replicates (both were missed by an
earlier version that only sent single-field payloads):

  * DIALOG ECHO — both room edit dialogs (RoomForm on StructurePage,
    RoomEditRow on PlanAnalysisPage) always resubmit the pre-filled
    height even when the user only changed the name. An unchanged
    echo must NOT promote the source to ``manual`` (that would
    silently detach ``floor`` rooms and put a lying "Manuell" badge
    on ``schnitt``/``grundriss`` rooms).
  * WHITELIST SPLIT — the API-facing whitelist (``rooms.py``) must
    accept ``floor`` (backend-minted, survives round-trips) while the
    Vision-facing whitelist (``pipeline.py``) must REJECT it: only
    the backend may mint ``floor``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.buildings import update_floor
from app.api.rooms import (
    _CEILING_SOURCE_VALUES as ROOMS_CEILING_SOURCES,
    _normalise_ceiling_source,
    _recalculate_walls_and_persist,
    bulk_calculate_walls,
    create_room,
    update_room,
)
from app.db.models.plan import Plan
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.plan_analysis.pipeline import (
    _CEILING_SOURCE_VALUES as PIPELINE_CEILING_SOURCES,
    _store_extraction_result,
)
from app.schemas.project import FloorUpdate
from app.schemas.room import RoomCreate, RoomUpdate


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_floor(
    db: AsyncSession, *, floor_height_m: Decimal | None = None
) -> tuple[User, Floor, Unit]:
    """User → Project → Building → Floor → Unit, floor height optional."""
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
        id=uuid.uuid4(),
        building_id=building.id,
        name="EG",
        floor_height_m=floor_height_m,
    )
    db.add(floor)
    await db.flush()

    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top")
    db.add(unit)
    await db.flush()
    return user, floor, unit


def _room(
    unit_id: uuid.UUID,
    name: str,
    *,
    height: Decimal | None,
    source: str,
    wall_gross: Decimal | None = None,
) -> Room:
    """Room with a fixed 12 m perimeter so expected gross areas are
    trivially ``12 × height`` (all test heights stay below the 3,00 m
    surcharge ladder unless noted). ``wall_gross`` pre-seeds the cache
    so tests can prove a room was NOT recalculated."""
    return Room(
        id=uuid.uuid4(),
        unit_id=unit_id,
        name=name,
        area_m2=Decimal("16.0"),
        perimeter_m=Decimal("12.0"),
        perimeter_source="manual",
        height_m=height,
        ceiling_height_source=source,
        wall_area_gross_m2=wall_gross,
    )


# ---------------------------------------------------------------------------
# Whitelist split — the most dangerous spot. The API-facing set
# (rooms.py) must ACCEPT ``floor`` (backend-minted marker, survives
# round-trips); the Vision-facing set (pipeline.py) must REJECT it
# (only the backend may mint ``floor`` — a hallucinated Vision claim
# would tag a real extracted height as an overwritable placeholder).
# ---------------------------------------------------------------------------


def test_floor_is_backend_only_across_the_two_whitelists():
    assert "floor" in ROOMS_CEILING_SOURCES, (
        "rooms.py whitelist lost 'floor' — the marker would silently "
        "collapse to 'default' on the next normalisation"
    )
    assert _normalise_ceiling_source("floor") == "floor"
    assert "floor" not in PIPELINE_CEILING_SOURCES, (
        "pipeline.py whitelist accepts 'floor' from Vision — a "
        "hallucinated claim would mark a real extracted height as an "
        "overwritable placeholder (only the backend may mint 'floor')"
    )


# ---------------------------------------------------------------------------
# PUT /floors/{id} — setting, changing, clearing the Geschoss-Höhe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_floor_height_reaches_only_default_rooms(
    db_session: AsyncSession,
):
    """Proof for the priority matrix: after setting the Geschoss-Höhe,
    ONLY the ``default`` room carries it. ``manual``, ``schnitt`` and
    ``grundriss`` rooms keep their measured heights AND their (stale-
    seeded) wall caches — they were not even recalculated."""
    user, floor, unit = await _seed_floor(db_session)
    r_manual = _room(
        unit.id, "Manuell", height=Decimal("2.80"), source="manual",
        wall_gross=Decimal("99.0"),
    )
    r_schnitt = _room(
        unit.id, "Schnitt", height=Decimal("3.10"), source="schnitt",
        wall_gross=Decimal("99.0"),
    )
    r_grundriss = _room(
        unit.id, "Grundriss", height=Decimal("2.60"), source="grundriss",
        wall_gross=Decimal("99.0"),
    )
    r_default = _room(
        unit.id, "Standard", height=Decimal("2.50"), source="default",
        wall_gross=Decimal("99.0"),
    )
    db_session.add_all([r_manual, r_schnitt, r_grundriss, r_default])
    await db_session.commit()

    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(floor_height_m=2.75),
        user=user,
        db=db_session,
    )
    await db_session.flush()

    fresh_default = await db_session.get(Room, r_default.id)
    assert float(fresh_default.height_m) == 2.75
    assert fresh_default.ceiling_height_source == "floor"
    # 12 m × 2,75 m × factor 1,0 — the cache was really recomputed.
    assert float(fresh_default.wall_area_gross_m2) == 33.0

    for untouched, expected_height in (
        (r_manual, 2.80),
        (r_schnitt, 3.10),
        (r_grundriss, 2.60),
    ):
        fresh = await db_session.get(Room, untouched.id)
        assert float(fresh.height_m) == expected_height, fresh.name
        assert fresh.ceiling_height_source == untouched.ceiling_height_source
        # Stale-seeded cache still 99.0 → no recalc ran for this room.
        assert float(fresh.wall_area_gross_m2) == 99.0, fresh.name


@pytest.mark.asyncio
async def test_change_floor_height_recalcs_floor_rooms_but_not_manual(
    db_session: AsyncSession,
):
    """Changing an existing Geschoss-Höhe re-resolves ``floor`` rooms
    (new height + fresh wall areas) while a ``manual`` room keeps
    both its height and its untouched cache."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    r_floor = _room(
        unit.id, "Vorgabe", height=Decimal("2.75"), source="floor",
        wall_gross=Decimal("33.0"),
    )
    r_manual = _room(
        unit.id, "Manuell", height=Decimal("2.80"), source="manual",
        wall_gross=Decimal("99.0"),
    )
    db_session.add_all([r_floor, r_manual])
    await db_session.commit()

    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(floor_height_m=2.85),
        user=user,
        db=db_session,
    )
    await db_session.flush()

    fresh_floor = await db_session.get(Room, r_floor.id)
    assert float(fresh_floor.height_m) == 2.85
    assert fresh_floor.ceiling_height_source == "floor"
    # 12 × 2,85 × 1,0
    assert float(fresh_floor.wall_area_gross_m2) == 34.2

    fresh_manual = await db_session.get(Room, r_manual.id)
    assert float(fresh_manual.height_m) == 2.80
    assert fresh_manual.ceiling_height_source == "manual"
    assert float(fresh_manual.wall_area_gross_m2) == 99.0


@pytest.mark.asyncio
async def test_clear_floor_height_reverts_floor_rooms_to_default(
    db_session: AsyncSession,
):
    """Clearing the Geschoss-Höhe drops ``floor`` rooms cleanly back
    to the 2,50 m default (source ``default``, cache recomputed);
    measured rooms stay untouched."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    r_floor = _room(
        unit.id, "Vorgabe", height=Decimal("2.75"), source="floor",
        wall_gross=Decimal("33.0"),
    )
    r_schnitt = _room(
        unit.id, "Schnitt", height=Decimal("3.10"), source="schnitt",
        wall_gross=Decimal("99.0"),
    )
    db_session.add_all([r_floor, r_schnitt])
    await db_session.commit()

    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(floor_height_m=None),
        user=user,
        db=db_session,
    )
    await db_session.flush()

    fresh_floor = await db_session.get(Room, r_floor.id)
    assert float(fresh_floor.height_m) == 2.5
    assert fresh_floor.ceiling_height_source == "default"
    # 12 × 2,50 × 1,0
    assert float(fresh_floor.wall_area_gross_m2) == 30.0

    fresh_schnitt = await db_session.get(Room, r_schnitt.id)
    assert float(fresh_schnitt.height_m) == 3.10
    assert fresh_schnitt.ceiling_height_source == "schnitt"
    assert float(fresh_schnitt.wall_area_gross_m2) == 99.0


@pytest.mark.asyncio
async def test_floor_update_without_height_change_skips_recalc(
    db_session: AsyncSession,
):
    """Renaming a floor must not trigger the fan-out — the recalc is
    guarded on ``floor_height_m`` actually being in the payload."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    r_default = _room(
        unit.id, "Standard", height=Decimal("2.50"), source="default",
        wall_gross=Decimal("99.0"),
    )
    db_session.add(r_default)
    await db_session.commit()

    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(name="EG neu"),
        user=user,
        db=db_session,
    )
    await db_session.flush()

    fresh = await db_session.get(Room, r_default.id)
    assert float(fresh.height_m) == 2.50
    assert fresh.ceiling_height_source == "default"
    assert float(fresh.wall_area_gross_m2) == 99.0


# ---------------------------------------------------------------------------
# PUT /rooms/{id} — priority of manual edits, and the fall-back
# product decision when a manual height is cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_height_survives_room_recalc_under_floor_height(
    db_session: AsyncSession,
):
    """A room the user typed a height into keeps that height across
    an unrelated inline edit, even though its floor has a
    Geschoss-Höhe — ``manual`` beats ``floor``."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Salon", height=Decimal("2.90"), source="manual")
    db_session.add(room)
    await db_session.commit()

    # Unrelated edit — the recalc (incl. Geschoss-Höhe resolution)
    # runs on every PUT, so this is exactly where an unguarded
    # fan-out would clobber the manual value.
    await update_room(
        room_id=room.id,
        data=RoomUpdate(name="Salon groß"),
        user=user,
        db=db_session,
    )

    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.90
    assert fresh.ceiling_height_source == "manual"


@pytest.mark.asyncio
async def test_typing_height_on_floor_room_promotes_to_manual(
    db_session: AsyncSession,
):
    """User overrides an inherited Geschoss-Höhe by typing → the room
    becomes ``manual`` and a later floor-height change ignores it."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Bad", height=Decimal("2.75"), source="floor")
    db_session.add(room)
    await db_session.commit()

    await update_room(
        room_id=room.id,
        data=RoomUpdate(height_m=2.9),
        user=user,
        db=db_session,
    )
    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.90
    assert fresh.ceiling_height_source == "manual"

    # Floor height changes afterwards — the promoted room must not move.
    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(floor_height_m=2.6),
        user=user,
        db=db_session,
    )
    await db_session.flush()
    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.90
    assert fresh.ceiling_height_source == "manual"


@pytest.mark.asyncio
async def test_clearing_manual_height_falls_back_to_floor_height(
    db_session: AsyncSession,
):
    """Product decision v24.6: deleting a manual height falls back to
    the Geschoss-Höhe (source ``floor``) — the Stockwerk-Vorgabe is
    the better default than the bare 2,50."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Küche", height=Decimal("2.90"), source="manual")
    db_session.add(room)
    await db_session.commit()

    await update_room(
        room_id=room.id,
        data=RoomUpdate(height_m=None),
        user=user,
        db=db_session,
    )

    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.75
    assert fresh.ceiling_height_source == "floor"


@pytest.mark.asyncio
async def test_clearing_manual_height_without_floor_height_gives_default(
    db_session: AsyncSession,
):
    """Without a Geschoss-Höhe the pre-v24.6 behaviour stands:
    clearing a height lands on 2,50 / ``default``."""
    user, floor, unit = await _seed_floor(db_session, floor_height_m=None)
    room = _room(unit.id, "Küche", height=Decimal("2.90"), source="manual")
    db_session.add(room)
    await db_session.commit()

    await update_room(
        room_id=room.id,
        data=RoomUpdate(height_m=None),
        user=user,
        db=db_session,
    )

    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.5
    assert fresh.ceiling_height_source == "default"


# ---------------------------------------------------------------------------
# DIALOG ECHO — the real payload both edit dialogs send: the user
# changes the NAME, the form resubmits the pre-filled height. This is
# exactly the gap the earlier single-field tests missed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dialog_echo_edit_keeps_floor_room_attached(
    db_session: AsyncSession,
):
    """Rename via edit dialog (name + unchanged height echoed) must
    NOT detach a ``floor`` room — and the room must still follow the
    next Geschoss-Höhen-Änderung afterwards."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Zimmer 1", height=Decimal("2.75"), source="floor")
    db_session.add(room)
    await db_session.commit()

    # The real dialog payload: new name + echoed, unchanged height.
    await update_room(
        room_id=room.id,
        data=RoomUpdate(name="Schlafzimmer", height_m=2.75),
        user=user,
        db=db_session,
    )
    fresh = await db_session.get(Room, room.id)
    assert fresh.name == "Schlafzimmer"
    assert float(fresh.height_m) == 2.75
    assert fresh.ceiling_height_source == "floor", (
        "unchanged dialog echo detached the room to 'manual'"
    )

    # Proof of continued attachment: the next deliberate
    # Geschoss-Höhen-Änderung still reaches the renamed room.
    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(floor_height_m=2.85),
        user=user,
        db=db_session,
    )
    await db_session.flush()
    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.85
    assert fresh.ceiling_height_source == "floor"


@pytest.mark.asyncio
async def test_dialog_echo_edit_keeps_schnitt_source(
    db_session: AsyncSession,
):
    """The echo guard also stops the pre-existing lie: a rename must
    not flip a ``schnitt`` measurement to a 'Manuell' badge."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Bad", height=Decimal("3.10"), source="schnitt")
    db_session.add(room)
    await db_session.commit()

    await update_room(
        room_id=room.id,
        data=RoomUpdate(name="Bad OG", height_m=3.10),
        user=user,
        db=db_session,
    )
    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 3.10
    assert fresh.ceiling_height_source == "schnitt"


@pytest.mark.asyncio
async def test_changed_height_in_dialog_still_promotes_to_manual(
    db_session: AsyncSession,
):
    """The guard must not overshoot: a genuinely NEW height typed in
    the dialog (alongside other fields) is a manual measurement."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Küche", height=Decimal("2.75"), source="floor")
    db_session.add(room)
    await db_session.commit()

    await update_room(
        room_id=room.id,
        data=RoomUpdate(name="Küche neu", height_m=2.80),
        user=user,
        db=db_session,
    )
    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.80
    assert fresh.ceiling_height_source == "manual"


# ---------------------------------------------------------------------------
# Alt-Daten (Option A) — dormant pre-v24.6 floor heights must stay
# inert: touching a room never adopts them; only a deliberate
# Geschoss-Höhen-Änderung (real new value) activates them.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_project_room_edit_leaves_dormant_floor_height_inert(
    db_session: AsyncSession,
):
    """The decided Alt-Daten scenario: old project, stored (dormant)
    Geschoss-Höhe 2,30, room at 2,50/'default'. Renaming the room —
    with or without the dialog's height echo — must leave it at
    2,50/'default', NOT jump to 2,30."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.30")
    )
    room = _room(unit.id, "Lager", height=Decimal("2.50"), source="default")
    db_session.add(room)
    await db_session.commit()

    # Name-only edit (e.g. API client or future slim dialog).
    await update_room(
        room_id=room.id,
        data=RoomUpdate(name="Lager alt"),
        user=user,
        db=db_session,
    )
    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.50
    assert fresh.ceiling_height_source == "default"

    # Dialog edit with echoed, unchanged height.
    await update_room(
        room_id=room.id,
        data=RoomUpdate(name="Lager neu", height_m=2.50),
        user=user,
        db=db_session,
    )
    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.50, (
        "room edit retroactively activated a dormant floor height"
    )
    assert fresh.ceiling_height_source == "default"


@pytest.mark.asyncio
async def test_floor_form_echo_does_not_activate_dormant_height(
    db_session: AsyncSession,
):
    """The floor form echoes the pre-filled Raumhöhe on every save.
    Renaming a Stockwerk (unchanged height echoed) must not fan out —
    otherwise every floor rename would retroactively activate dormant
    heights."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.30")
    )
    room = _room(
        unit.id, "Keller", height=Decimal("2.50"), source="default",
        wall_gross=Decimal("99.0"),
    )
    db_session.add(room)
    await db_session.commit()

    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(name="Keller umbenannt", floor_height_m=2.30),
        user=user,
        db=db_session,
    )
    await db_session.flush()

    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.50
    assert fresh.ceiling_height_source == "default"
    # Stale-seeded cache untouched → no recalc ran at all.
    assert float(fresh.wall_area_gross_m2) == 99.0


@pytest.mark.asyncio
async def test_null_floor_rename_stays_inert(
    db_session: AsyncSession,
):
    """Regression for the Fix-2 gap: a Stockwerk WITHOUT a Geschoss-
    Höhe (floor_height_m NULL — the pre-v24.6 / pipeline-created
    state) must stay inert on a bare rename.

    After the frontend fix the FloorForm no longer pre-fills 2,50 into
    an empty height field, so a name-only edit sends
    ``floor_height_m=None`` (empty input) rather than a phantom 2,50.
    The backend must then NOT fan out: old=None, new=None →
    height_really_changed is False. Rooms keep their honest
    2,50/'default' — no phantom Geschoss-Höhe, no 'default'→'floor'
    flip, and (crucially) a real height that happened to land as
    'default' is not overwritten."""
    user, floor, unit = await _seed_floor(db_session, floor_height_m=None)
    r_default = _room(
        unit.id, "Standard", height=Decimal("2.50"), source="default",
        wall_gross=Decimal("99.0"),
    )
    # A real extracted height that collapsed to 'default' (unrecognised
    # Vision source) — the destructive case from the review: it must
    # survive a floor rename untouched.
    r_real = _room(
        unit.id, "Wohnen", height=Decimal("2.65"), source="default",
        wall_gross=Decimal("99.0"),
    )
    db_session.add_all([r_default, r_real])
    await db_session.commit()

    await update_floor(
        floor_id=floor.id,
        data=FloorUpdate(name="EG umbenannt", floor_height_m=None),
        user=user,
        db=db_session,
    )
    await db_session.flush()

    fresh_default = await db_session.get(Room, r_default.id)
    assert float(fresh_default.height_m) == 2.50
    assert fresh_default.ceiling_height_source == "default"
    assert float(fresh_default.wall_area_gross_m2) == 99.0

    fresh_real = await db_session.get(Room, r_real.id)
    assert float(fresh_real.height_m) == 2.65, (
        "floor rename overwrote a real height on a NULL-height floor"
    )
    assert fresh_real.ceiling_height_source == "default"
    assert float(fresh_real.wall_area_gross_m2) == 99.0

    # The floor itself gained no phantom Geschoss-Höhe.
    fresh_floor = await db_session.get(Floor, floor.id)
    assert fresh_floor.floor_height_m is None


@pytest.mark.asyncio
async def test_bulk_recalc_does_not_adopt_floor_height(
    db_session: AsyncSession,
):
    """The "Wandflächen berechnen" button recomputes caches but must
    not re-source rooms (Option A): a 'default' room stays at 2,50
    even though the floor has a Geschoss-Höhe."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Flur", height=Decimal("2.50"), source="default")
    db_session.add(room)
    await db_session.commit()

    project = (await db_session.execute(select(Project))).scalars().first()
    await bulk_calculate_walls(
        project_id=project.id,
        user=user,
        db=db_session,
    )

    fresh = await db_session.get(Room, room.id)
    assert float(fresh.height_m) == 2.50
    assert fresh.ceiling_height_source == "default"
    # The recalc itself DID run — cache is fresh (12 × 2,50 × 1,0).
    assert float(fresh.wall_area_gross_m2) == 30.0


# ---------------------------------------------------------------------------
# POST /units/{id}/rooms — new manual rooms inherit the Geschoss-Höhe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_room_without_height_inherits_floor_height(
    db_session: AsyncSession,
):
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )

    created = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Neu", area_m2=16.0, perimeter_m=12.0),
        user=user,
        db=db_session,
    )

    assert float(created.height_m) == 2.75
    assert created.ceiling_height_source == "floor"
    # 12 × 2,75 × 1,0 — the wall cache is computed with the inherited
    # height, not the 2,50 default.
    assert float(created.wall_area_gross_m2) == 33.0


@pytest.mark.asyncio
async def test_create_room_with_typed_height_stays_manual(
    db_session: AsyncSession,
):
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )

    created = await create_room(
        unit_id=unit.id,
        data=RoomCreate(name="Neu", area_m2=16.0, perimeter_m=12.0, height_m=2.9),
        user=user,
        db=db_session,
    )

    assert float(created.height_m) == 2.90
    assert created.ceiling_height_source == "manual"


# ---------------------------------------------------------------------------
# KI-Pipeline — extracted rooms without a height inherit the
# Geschoss-Höhe; real Vision heights win
# ---------------------------------------------------------------------------


async def _seed_plan_with_floor(
    db: AsyncSession, *, floor_height_m: Decimal | None
) -> Plan:
    """Project + Building "Gebäude 1" + Floor "EG" (so the pipeline
    reuses them instead of creating fresh ones) + a Plan row."""
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

    building = Building(
        id=uuid.uuid4(), project_id=project.id, name="Gebäude 1"
    )
    db.add(building)
    await db.flush()

    floor = Floor(
        id=uuid.uuid4(),
        building_id=building.id,
        name="EG",
        level_number=0,
        floor_height_m=floor_height_m,
    )
    db.add(floor)
    await db.flush()

    plan = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="grundriss.pdf",
        file_path="/tmp/grundriss.pdf",
        analysis_status="processing",
        created_at=datetime.now(timezone.utc),
    )
    db.add(plan)
    await db.flush()
    return plan


def _vision_result(rooms: list[dict]) -> dict:
    return {
        "floor_name": "EG",
        "floor_level": 0,
        "units": [
            {"unit_name": "Top 1", "unit_type": "wohnung", "rooms": rooms}
        ],
    }


@pytest.mark.asyncio
async def test_pipeline_room_without_height_inherits_floor_height(
    db_session: AsyncSession,
):
    """Grundriss-only upload (no heights extractable) on a floor with
    a Geschoss-Höhe → rooms are born with that height, source
    ``floor``, and the eager wall calc uses it."""
    plan = await _seed_plan_with_floor(
        db_session, floor_height_m=Decimal("2.62")
    )

    created = await _store_extraction_result(
        _vision_result(
            [{"room_name": "Wohnzimmer", "area_m2": 24.5, "perimeter_m": 20.0}]
        ),
        plan,
        db_session,
    )
    await db_session.commit()
    assert created == 1

    room = (await db_session.execute(select(Room))).scalars().first()
    assert room is not None
    assert abs(float(room.height_m) - 2.62) < 1e-6
    assert room.ceiling_height_source == "floor"
    # 20 × 2,62 × 1,0 — eager calc ran against the inherited height.
    assert float(room.wall_area_gross_m2) == 52.4


@pytest.mark.asyncio
async def test_pipeline_extracted_height_beats_floor_height(
    db_session: AsyncSession,
):
    """A real Vision height (source ``grundriss``) must NOT be
    replaced by the Geschoss-Höhe."""
    plan = await _seed_plan_with_floor(
        db_session, floor_height_m=Decimal("2.62")
    )

    await _store_extraction_result(
        _vision_result(
            [
                {
                    "room_name": "Wohnzimmer",
                    "area_m2": 24.5,
                    "perimeter_m": 20.0,
                    "height_m": 2.4,
                    "ceiling_height_source": "grundriss",
                }
            ]
        ),
        plan,
        db_session,
    )
    await db_session.commit()

    room = (await db_session.execute(select(Room))).scalars().first()
    assert room is not None
    assert abs(float(room.height_m) - 2.4) < 1e-6
    assert room.ceiling_height_source == "grundriss"


@pytest.mark.asyncio
async def test_pipeline_collapses_vision_floor_claim_to_default(
    db_session: AsyncSession,
):
    """Only the backend may mint ``floor``. If Vision hallucinates
    ``ceiling_height_source='floor'`` alongside a real extracted
    height, the claim collapses to ``default`` and the measured value
    survives — it must not be persisted as an overwritable
    ``floor`` placeholder."""
    plan = await _seed_plan_with_floor(db_session, floor_height_m=None)

    await _store_extraction_result(
        _vision_result(
            [
                {
                    "room_name": "Wohnzimmer",
                    "area_m2": 24.5,
                    "perimeter_m": 20.0,
                    "height_m": 2.7,
                    "ceiling_height_source": "floor",
                }
            ]
        ),
        plan,
        db_session,
    )
    await db_session.commit()

    room = (await db_session.execute(select(Room))).scalars().first()
    assert room is not None
    assert abs(float(room.height_m) - 2.7) < 1e-6
    assert room.ceiling_height_source == "default"


@pytest.mark.asyncio
async def test_pipeline_without_floor_height_keeps_default_behaviour(
    db_session: AsyncSession,
):
    """No Geschoss-Höhe set → pre-v24.6 behaviour: 2,50 write-back,
    source ``default``."""
    plan = await _seed_plan_with_floor(db_session, floor_height_m=None)

    await _store_extraction_result(
        _vision_result(
            [{"room_name": "Wohnzimmer", "area_m2": 24.5, "perimeter_m": 20.0}]
        ),
        plan,
        db_session,
    )
    await db_session.commit()

    room = (await db_session.execute(select(Room))).scalars().first()
    assert room is not None
    assert abs(float(room.height_m) - 2.5) < 1e-6
    assert room.ceiling_height_source == "default"


# ---------------------------------------------------------------------------
# Backward compatibility — helper without db skips the resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalc_without_db_skips_floor_resolution(
    db_session: AsyncSession,
):
    """Legacy callers (and the pre-existing tests) invoke the recalc
    helper without a session — they must keep the pre-v24.6
    behaviour even when a Geschoss-Höhe exists."""
    user, floor, unit = await _seed_floor(
        db_session, floor_height_m=Decimal("2.75")
    )
    room = _room(unit.id, "Alt", height=Decimal("2.50"), source="default")
    db_session.add(room)
    await db_session.commit()

    stmt = (
        select(Room).where(Room.id == room.id).options(selectinload(Room.openings))
    )
    fresh = (await db_session.execute(stmt)).scalars().first()
    assert fresh is not None
    await _recalculate_walls_and_persist(fresh)
    await db_session.flush()

    assert float(fresh.height_m) == 2.5
    assert fresh.ceiling_height_source == "default"
