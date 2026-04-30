from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models.project import Room, Opening, Unit, Floor, Building
from app.db.models.user import User
from app.schemas.room import (
    RoomCreate, RoomUpdate, RoomResponse,
    OpeningCreate, OpeningUpdate, OpeningResponse,
    WallCalculationResponse, BulkWallCalculationResponse,
)
from app.services.wall_calculator import (
    calculate_wall_areas,
    estimate_perimeter_from_area,
    openings_from_orm,
)
from app.auth import get_current_user
from app.api.ownership import (
    verify_project_owner, verify_unit_owner,
    verify_room_owner, verify_opening_owner,
)

router = APIRouter()


# Accepted ceiling_height_source values, shared with the pipeline.
# Values outside this set collapse to ``default`` so the DB can't end
# up with a stray typo that the frontend's amber-warning logic
# wouldn't recognise.
_CEILING_SOURCE_VALUES = {"schnitt", "grundriss", "manual", "default"}


def _normalise_ceiling_source(value: str | None) -> str:
    if value in _CEILING_SOURCE_VALUES:
        return value
    return "default"


async def _recalculate_walls_and_persist(room: Room) -> WallCalculationResponse:
    """Run the calculator for one room, write results back, return payload.

    The caller must have loaded ``room.openings`` via ``selectinload``
    (otherwise accessing the relationship raises ``MissingGreenlet``
    in async code). The caller is also responsible for flushing the
    session — we only mutate attributes so the write batches with any
    other work in the same request.

    Auto-estimate guarantee
    -----------------------
    If the room hits the recalc with ``perimeter_m`` missing but
    ``area_m2`` populated, we fill the perimeter with a 4·√A·1.10
    estimate before computing the wall areas — so the user never
    sees gross 0,00 m² on a row that has enough information to
    yield a sensible number. The new perimeter is tagged
    ``perimeter_source='estimated'`` exactly like the pipeline
    would tag it, so the UI can show the same subtle hint instead
    of the red "Bitte eintragen" badge. This benefits every entry
    point that flows through ``_recalculate_walls_and_persist``:
    POST /rooms (manual create), PUT /rooms/{id} (inline edit),
    POST /rooms/{id}/calculate-walls and the bulk variant.

    Default-height write-back
    -------------------------
    When ``height_m`` was null, ``calculate_wall_areas`` substitutes
    the 2,50 m fallback for its own arithmetic — but used to leave
    ``room.height_m`` itself null in the DB. That produced an ugly
    inconsistency in the wall-calc table: the row showed gross/net
    computed against 2,50 m yet the Deckenhöhe column rendered the
    red "Bitte eintragen" empty-state badge. We now write the
    resolved value back so the DB is internally consistent — null
    height + ``ceiling_height_source = 'default'`` becomes
    ``height_m = 2.50`` + ``ceiling_height_source = 'default'``.
    The frontend then renders the value with the subtle "Standard"
    hint, exactly matching what the calc used.
    """

    # Auto-estimate guarantee — see docstring.
    if room.perimeter_m is None or float(room.perimeter_m) <= 0:
        estimated = estimate_perimeter_from_area(room.area_m2)
        if estimated is not None:
            room.perimeter_m = estimated
            room.perimeter_source = "estimated"

    calc = calculate_wall_areas(
        perimeter_m=float(room.perimeter_m) if room.perimeter_m is not None else None,
        height_m=float(room.height_m) if room.height_m is not None else None,
        is_staircase=bool(room.is_staircase),
        deductions_enabled=bool(room.deductions_enabled),
        openings=openings_from_orm(room.openings),
        ceiling_height_source=room.ceiling_height_source or "default",
    )
    room.wall_area_gross_m2 = calc.wall_area_gross_m2
    room.wall_area_net_m2 = calc.wall_area_net_m2
    room.applied_factor = calc.applied_factor
    # calculate_wall_areas may flip the source to "default" when
    # height was missing — keep the DB consistent with the UI signal.
    room.ceiling_height_source = calc.ceiling_height_source
    # Default-height write-back — see docstring. Only fires when the
    # row had no height to begin with; explicit user values are never
    # overwritten because ``calc.height_used_m == room.height_m``
    # in that branch and we'd be assigning the same value anyway.
    if room.height_m is None:
        room.height_m = calc.height_used_m

    return WallCalculationResponse(
        room_id=room.id,
        wall_area_gross_m2=calc.wall_area_gross_m2,
        wall_area_net_m2=calc.wall_area_net_m2,
        applied_factor=calc.applied_factor,
        deductions_total_m2=calc.deductions_total_m2,
        deductions_considered_count=calc.deductions_considered_count,
        perimeter_m=calc.perimeter_m,
        height_used_m=calc.height_used_m,
        ceiling_height_source=calc.ceiling_height_source,
    )


@router.post("/units/{unit_id}/rooms", response_model=RoomResponse, status_code=201)
async def create_room(
    unit_id: UUID,
    data: RoomCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_unit_owner(unit_id, user, db)
    openings_data = data.openings
    payload = data.model_dump(exclude={"openings"})

    # ----------------------------------------------------------------
    # ceiling_height_source — three branches.
    #
    # 1. Caller passed a source string explicitly → honour it (after
    #    normalisation against the canonical set).
    # 2. No source, no height (or height == 2.50, the Austrian
    #    residential standard fallback) → ``default``. Treating an
    #    explicit 2.50 the same as null protects against frontend
    #    regressions that pre-fill the field with the placeholder
    #    default and submit it untouched.
    # 3. No source, height ≠ 2.50 → ``manual``. The user typed a
    #    real measurement.
    # ----------------------------------------------------------------
    provided_source = payload.get("ceiling_height_source")
    provided_height = payload.get("height_m")
    if provided_source:
        payload["ceiling_height_source"] = _normalise_ceiling_source(
            provided_source
        )
    elif provided_height is None or float(provided_height) == 2.5:
        payload["ceiling_height_source"] = "default"
    else:
        payload["ceiling_height_source"] = "manual"

    # ----------------------------------------------------------------
    # perimeter_m + perimeter_source — three branches, mirror logic.
    #
    # 1. Caller passed an explicit ``perimeter_source`` → honour it.
    # 2. Caller passed a ``perimeter_m`` value → ``manual``.
    # 3. No perimeter but a positive ``area_m2`` → estimate
    #    (4 · √A · 1.10) and tag ``estimated``. Means a manual room
    #    creation with just an area never falls through to gross
    #    0,00 m² on first render.
    # 4. Otherwise → leave both null. The "Bitte eintragen"
    #    emergency-fallback badge fires in the UI.
    # ----------------------------------------------------------------
    provided_perimeter_source = payload.get("perimeter_source")
    provided_perimeter = payload.get("perimeter_m")
    provided_area = payload.get("area_m2")
    if provided_perimeter_source:
        # Explicit user choice — pass through.
        pass
    elif provided_perimeter is not None:
        payload["perimeter_source"] = "manual"
    else:
        estimated = estimate_perimeter_from_area(provided_area)
        if estimated is not None:
            payload["perimeter_m"] = estimated
            payload["perimeter_source"] = "estimated"
        # else: both stay null → empty-state badge in the UI.

    room = Room(
        unit_id=unit_id,
        source="manual",
        **payload,
    )
    db.add(room)
    await db.flush()

    for opening_data in openings_data:
        opening = Opening(room_id=room.id, source="manual", **opening_data.model_dump())
        db.add(opening)

    await db.flush()
    # Reload with openings so the wall calculation sees them.
    stmt = select(Room).where(Room.id == room.id).options(selectinload(Room.openings))
    result = await db.execute(stmt)
    room = result.scalars().first()
    await _recalculate_walls_and_persist(room)
    await db.flush()
    return room


@router.get("/projects/{project_id}/rooms", response_model=list[RoomResponse])
async def list_project_rooms(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all rooms for a project (across all buildings/floors/units)."""
    await verify_project_owner(project_id, user, db)
    stmt = (
        select(Room)
        .join(Unit).join(Floor).join(Building)
        .where(Building.project_id == project_id)
        .options(selectinload(Room.openings))
        .order_by(Room.name)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/rooms/{room_id}", response_model=RoomResponse)
async def get_room(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_room_owner(room_id, user, db)
    stmt = select(Room).where(Room.id == room_id).options(selectinload(Room.openings))
    result = await db.execute(stmt)
    room = result.scalars().first()
    if not room:
        raise HTTPException(404, "Raum nicht gefunden")
    return room


@router.put("/rooms/{room_id}", response_model=RoomResponse)
async def update_room(
    room_id: UUID,
    data: RoomUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_room_owner(room_id, user, db)
    # Reload with openings so the recalc below doesn't race the ORM
    # lazy-load in async mode.
    stmt = select(Room).where(Room.id == room_id).options(selectinload(Room.openings))
    result = await db.execute(stmt)
    room = result.scalars().first()
    if not room:
        raise HTTPException(404, "Raum nicht gefunden")

    updates = data.model_dump(exclude_unset=True)
    # Any user edit to a geometry- or deduction-relevant field is
    # treated as a manual override of the AI-sourced value — promote
    # the source marker so the "AI vs manual" badge in the UI is
    # correct after the first edit.
    if "ceiling_height_source" in updates:
        updates["ceiling_height_source"] = _normalise_ceiling_source(
            updates["ceiling_height_source"]
        )
    elif "height_m" in updates and updates["height_m"] is not None:
        # User typed a height but didn't specify the source → treat
        # the new value as manual.
        updates["ceiling_height_source"] = "manual"

    # Mirror behaviour for ``perimeter_source``: if the user supplies
    # a new perimeter (without explicitly setting the source), tag
    # the value as ``manual`` so the wall-calc table stops flagging
    # it as an estimate. ``None`` clears mean the user wants to
    # remove the value — we drop the source flag too so the row
    # falls back to the "Bitte eintragen" empty-state badge.
    if "perimeter_source" not in updates and "perimeter_m" in updates:
        updates["perimeter_source"] = (
            "manual" if updates["perimeter_m"] is not None else None
        )

    for key, value in updates.items():
        setattr(room, key, value)
    room.source = "manual"

    # Recompute walls so the cached gross/net stays in sync with the
    # new perimeter / height / deductions_enabled. Cheap — the
    # calculator is pure arithmetic.
    await _recalculate_walls_and_persist(room)
    await db.flush()
    return room


@router.delete("/rooms/{room_id}", status_code=204)
async def delete_room(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    room = await verify_room_owner(room_id, user, db)
    await db.delete(room)
    await db.flush()


@router.post(
    "/rooms/{room_id}/calculate-walls",
    response_model=WallCalculationResponse,
    tags=["Wandberechnung"],
)
async def calculate_walls_for_room(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recalculate wall areas for a single room and persist the result.

    Idempotent — safe to call repeatedly from the UI after the user
    changes a height, toggles deductions, or adds an opening. The
    endpoint returns the calculator's full payload so the frontend
    can update its row without re-fetching the whole rooms list.
    """
    await verify_room_owner(room_id, user, db)
    stmt = select(Room).where(Room.id == room_id).options(selectinload(Room.openings))
    result = await db.execute(stmt)
    room = result.scalars().first()
    if not room:
        raise HTTPException(404, "Raum nicht gefunden")
    payload = await _recalculate_walls_and_persist(room)
    await db.flush()
    return payload


@router.post(
    "/projects/{project_id}/rooms/bulk-calculate-walls",
    response_model=BulkWallCalculationResponse,
    tags=["Wandberechnung"],
)
async def bulk_calculate_walls(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recalculate wall areas for every room in a project.

    Intended as a one-click "Wandflächen berechnen" action after the
    user has confirmed or corrected ceiling heights. We load all
    rooms with their openings in one query and iterate; the
    calculator is synchronous pure-Python so there's no point in
    concurrency.

    Auto-fill behaviour
    -------------------
    For every room that has an area but no perimeter at the time of
    the call, the recalc step runs ``estimate_perimeter_from_area``
    inside ``_recalculate_walls_and_persist`` (see its docstring)
    and tags the resolved value ``perimeter_source = 'estimated'``.
    For every room that has no explicit height, the same step
    writes the 2,50 m default back so the cell renders cleanly in
    the wall-calc table. Together this means the user can click
    "Wandflächen berechnen" once on a project full of partially-
    extracted rooms and walk away with sensible numbers across the
    board — no per-room "Bitte eintragen" parade.
    """
    await verify_project_owner(project_id, user, db)
    stmt = (
        select(Room)
        .join(Unit).join(Floor).join(Building)
        .where(Building.project_id == project_id)
        .options(selectinload(Room.openings))
    )
    result = await db.execute(stmt)
    rooms = list(result.scalars().all())

    results: list[WallCalculationResponse] = []
    for room in rooms:
        # Defence in depth: the estimate also runs inside
        # ``_recalculate_walls_and_persist``, but doing it here
        # explicitly makes the "fill before recalc" intent visible
        # at the call site. Idempotent — both checks are guarded by
        # the same ``perimeter_m is None`` condition.
        if room.perimeter_m is None or float(room.perimeter_m) <= 0:
            estimated = estimate_perimeter_from_area(room.area_m2)
            if estimated is not None:
                room.perimeter_m = estimated
                room.perimeter_source = "estimated"

        payload = await _recalculate_walls_and_persist(room)
        results.append(payload)

    await db.flush()
    return BulkWallCalculationResponse(
        project_id=project_id,
        rooms_calculated=len(results),
        results=results,
    )


# --- Openings ---

@router.post("/rooms/{room_id}/openings", response_model=OpeningResponse, status_code=201)
async def create_opening(
    room_id: UUID,
    data: OpeningCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_room_owner(room_id, user, db)
    opening = Opening(room_id=room_id, source="manual", **data.model_dump())
    db.add(opening)
    await db.flush()
    # Keep the cached wall area in sync. Reload the room with openings
    # so the calc sees the row we just added.
    stmt = select(Room).where(Room.id == room_id).options(selectinload(Room.openings))
    room = (await db.execute(stmt)).scalars().first()
    if room is not None:
        await _recalculate_walls_and_persist(room)
        await db.flush()
    return opening


@router.put("/openings/{opening_id}", response_model=OpeningResponse)
async def update_opening(
    opening_id: UUID,
    data: OpeningUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opening = await verify_opening_owner(opening_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(opening, key, value)
    await db.flush()
    # Update the parent room's cached wall area too, since opening
    # geometry directly changes the deduction.
    stmt = (
        select(Room)
        .where(Room.id == opening.room_id)
        .options(selectinload(Room.openings))
    )
    room = (await db.execute(stmt)).scalars().first()
    if room is not None:
        await _recalculate_walls_and_persist(room)
        await db.flush()
    return opening


@router.delete("/openings/{opening_id}", status_code=204)
async def delete_opening(
    opening_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opening = await verify_opening_owner(opening_id, user, db)
    room_id = opening.room_id
    await db.delete(opening)
    await db.flush()
    # Recompute after the delete so the cached net area reflects the
    # removed opening.
    stmt = select(Room).where(Room.id == room_id).options(selectinload(Room.openings))
    room = (await db.execute(stmt)).scalars().first()
    if room is not None:
        await _recalculate_walls_and_persist(room)
        await db.flush()
