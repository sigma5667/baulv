from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.project import Building, Floor, Unit
from app.db.models.user import User
from app.schemas.project import (
    BuildingCreate, BuildingResponse,
    FloorCreate, FloorResponse,
    UnitCreate, UnitResponse,
)
from app.auth import get_current_user
from app.api.ownership import verify_project_owner, verify_building_owner, verify_floor_owner

router = APIRouter()


# --- Buildings ---

@router.post("/projects/{project_id}/buildings", response_model=BuildingResponse, status_code=201)
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


@router.get("/projects/{project_id}/buildings", response_model=list[BuildingResponse])
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
    data: BuildingCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    building = await verify_building_owner(building_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(building, key, value)
    await db.flush()
    return building


# --- Floors ---

@router.post("/buildings/{building_id}/floors", response_model=FloorResponse, status_code=201)
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


@router.get("/buildings/{building_id}/floors", response_model=list[FloorResponse])
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


# --- Units ---

@router.post("/floors/{floor_id}/units", response_model=UnitResponse, status_code=201)
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


@router.get("/floors/{floor_id}/units", response_model=list[UnitResponse])
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
