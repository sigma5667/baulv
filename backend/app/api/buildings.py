"""Gebäude → Stockwerk → Einheit CRUD.

The Room and Opening endpoints live in ``app/api/rooms.py``. Everything
*above* a room (the hierarchy the user navigates when building up the
project structure by hand) lives here, plus:

* a read-only hierarchical GET that returns the whole project tree
  in one payload (frontend renders a collapsible tree and would
  otherwise need N+1 round-trips),
* a "Schnell-Anlage: Einfamilienhaus" endpoint that seeds a plausible
  starter structure so a tester without a PDF plan can be productive
  in one click.

Cascade-delete behaviour comes from the SQLAlchemy relationships
(``cascade="all, delete-orphan"``) — deleting a building removes all
its floors/units/rooms/openings in one transaction.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models.project import Building, Floor, Unit, Room, Opening
from app.db.models.user import User
from app.schemas.project import (
    BuildingCreate, BuildingUpdate, BuildingResponse,
    FloorCreate, FloorUpdate, FloorResponse,
    UnitCreate, UnitUpdate, UnitResponse,
    ProjectStructureResponse,
    QuickAddResponse,
)
from app.auth import get_current_user
from app.api.ownership import (
    verify_project_owner,
    verify_building_owner,
    verify_floor_owner,
    verify_unit_owner,
)

router = APIRouter()


# --- Buildings ---

@router.post(
    "/projects/{project_id}/buildings",
    response_model=BuildingResponse,
    status_code=201,
)
async def create_building(
    project_id: UUID,
    data: BuildingCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_owner(project_id, user, db)
    building = Building(project_id=project_id, **data.model_dump())
    db.add(building)
    await db.flush()
    return building


@router.get(
    "/projects/{project_id}/buildings",
    response_model=list[BuildingResponse],
)
async def list_buildings(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_owner(project_id, user, db)
    result = await db.execute(
        select(Building).where(Building.project_id == project_id).order_by(Building.sort_order)
    )
    return result.scalars().all()


@router.put("/buildings/{building_id}", response_model=BuildingResponse)
async def update_building(
    building_id: UUID,
    data: BuildingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    building = await verify_building_owner(building_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(building, key, value)
    await db.flush()
    return building


@router.delete("/buildings/{building_id}", status_code=204)
async def delete_building(
    building_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Cascade="all, delete-orphan" on Building.floors → Floor.units →
    # Unit.rooms → Room.openings wipes the whole subtree in one
    # transaction. The 204 tells the UI it's safe to drop the node
    # from the tree without re-fetching individual subtrees.
    building = await verify_building_owner(building_id, user, db)
    await db.delete(building)
    await db.flush()


# --- Floors ---

@router.post(
    "/buildings/{building_id}/floors",
    response_model=FloorResponse,
    status_code=201,
)
async def create_floor(
    building_id: UUID,
    data: FloorCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_building_owner(building_id, user, db)
    floor = Floor(building_id=building_id, **data.model_dump())
    db.add(floor)
    await db.flush()
    return floor


@router.get(
    "/buildings/{building_id}/floors",
    response_model=list[FloorResponse],
)
async def list_floors(
    building_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_building_owner(building_id, user, db)
    result = await db.execute(
        select(Floor).where(Floor.building_id == building_id).order_by(Floor.sort_order)
    )
    return result.scalars().all()


@router.put("/floors/{floor_id}", response_model=FloorResponse)
async def update_floor(
    floor_id: UUID,
    data: FloorUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    floor = await verify_floor_owner(floor_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(floor, key, value)
    await db.flush()
    return floor


@router.delete("/floors/{floor_id}", status_code=204)
async def delete_floor(
    floor_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    floor = await verify_floor_owner(floor_id, user, db)
    await db.delete(floor)
    await db.flush()


# --- Units ---

@router.post(
    "/floors/{floor_id}/units",
    response_model=UnitResponse,
    status_code=201,
)
async def create_unit(
    floor_id: UUID,
    data: UnitCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_floor_owner(floor_id, user, db)
    unit = Unit(floor_id=floor_id, **data.model_dump())
    db.add(unit)
    await db.flush()
    return unit


@router.get(
    "/floors/{floor_id}/units",
    response_model=list[UnitResponse],
)
async def list_units(
    floor_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_floor_owner(floor_id, user, db)
    result = await db.execute(
        select(Unit).where(Unit.floor_id == floor_id).order_by(Unit.sort_order)
    )
    return result.scalars().all()


@router.put("/units/{unit_id}", response_model=UnitResponse)
async def update_unit(
    unit_id: UUID,
    data: UnitUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    unit = await verify_unit_owner(unit_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(unit, key, value)
    await db.flush()
    return unit


@router.delete("/units/{unit_id}", status_code=204)
async def delete_unit(
    unit_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    unit = await verify_unit_owner(unit_id, user, db)
    await db.delete(unit)
    await db.flush()


# --- Full project structure (single-shot tree fetch) ---

@router.get(
    "/projects/{project_id}/structure",
    response_model=ProjectStructureResponse,
    tags=["Structure"],
)
async def get_project_structure(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the full Gebäude → Stockwerk → Einheit → Raum → Öffnung tree.

    The frontend renders this as a collapsible tree. One nested query
    beats the N+1 alternative of "list buildings, then per-building
    list floors, then per-floor list units, then per-unit list rooms"
    — which is what the old frontend had to do because no such
    aggregate endpoint existed.
    """
    await verify_project_owner(project_id, user, db)
    stmt = (
        select(Building)
        .where(Building.project_id == project_id)
        .options(
            selectinload(Building.floors)
            .selectinload(Floor.units)
            .selectinload(Unit.rooms)
            .selectinload(Room.openings)
        )
        .order_by(Building.sort_order, Building.name)
    )
    result = await db.execute(stmt)
    buildings = list(result.scalars().all())
    return ProjectStructureResponse(
        project_id=project_id,
        buildings=buildings,
    )


# --- Quick add: Schnell-Anlage Einfamilienhaus ---

@router.post(
    "/projects/{project_id}/quick-add/single-family",
    response_model=QuickAddResponse,
    status_code=201,
    tags=["Structure"],
)
async def quick_add_single_family(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Seed a typical single-family-house skeleton the tester can extend.

    Creates:

    * Gebäude "Haupthaus"
    * Stockwerke: "Keller" (level_number -1), "EG" (0), "OG" (1)
    * One Einheit per floor ("Standard") so the user can start adding
      rooms immediately without having to carve the floor into units
      first.

    Does **not** create any rooms — the whole point of this feature is
    that the user enters the rooms.

    Returns the IDs so the UI can scroll-to / expand the new nodes.
    If the project already has a Gebäude called "Haupthaus" we refuse
    with a 400 (the quick-add is a one-shot, not an appender).
    """
    await verify_project_owner(project_id, user, db)

    # Pre-flight: don't duplicate. Testers who click twice would
    # otherwise end up with "Haupthaus" and "Haupthaus" with identical
    # child trees.
    existing = await db.execute(
        select(Building).where(
            Building.project_id == project_id,
            Building.name == "Haupthaus",
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            400,
            "Ein Gebäude 'Haupthaus' existiert bereits. Schnell-Anlage ist "
            "nur für leere Projekte gedacht — bitte Räume direkt unter dem "
            "bestehenden Gebäude anlegen.",
        )

    building = Building(project_id=project_id, name="Haupthaus", sort_order=0)
    db.add(building)
    await db.flush()

    floors_spec = [
        ("Keller", -1, 2.3, 0),
        ("EG", 0, 2.5, 1),
        ("OG", 1, 2.5, 2),
    ]
    created_floor_ids: list[UUID] = []
    created_unit_ids: list[UUID] = []
    for name, level, height, order in floors_spec:
        floor = Floor(
            building_id=building.id,
            name=name,
            level_number=level,
            floor_height_m=height,
            sort_order=order,
        )
        db.add(floor)
        await db.flush()
        created_floor_ids.append(floor.id)

        unit = Unit(
            floor_id=floor.id,
            name="Standard",
            unit_type="wohnung",
            sort_order=0,
        )
        db.add(unit)
        await db.flush()
        created_unit_ids.append(unit.id)

    return QuickAddResponse(
        project_id=project_id,
        building_id=building.id,
        floor_ids=created_floor_ids,
        unit_ids=created_unit_ids,
    )
