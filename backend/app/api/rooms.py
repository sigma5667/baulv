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
)
from app.auth import get_current_user
from app.api.ownership import (
    verify_project_owner, verify_unit_owner,
    verify_room_owner, verify_opening_owner,
)

router = APIRouter()


@router.post("/units/{unit_id}/rooms", response_model=RoomResponse, status_code=201)
async def create_room(
    unit_id: UUID,
    data: RoomCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_unit_owner(unit_id, user, db)
    openings_data = data.openings
    room = Room(
        unit_id=unit_id,
        source="manual",
        **data.model_dump(exclude={"openings"}),
    )
    db.add(room)
    await db.flush()

    for opening_data in openings_data:
        opening = Opening(room_id=room.id, source="manual", **opening_data.model_dump())
        db.add(opening)

    await db.flush()
    stmt = select(Room).where(Room.id == room.id).options(selectinload(Room.openings))
    result = await db.execute(stmt)
    return result.scalars().first()


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
    room = await verify_room_owner(room_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(room, key, value)
    room.source = "manual"
    await db.flush()
    stmt = select(Room).where(Room.id == room_id).options(selectinload(Room.openings))
    result = await db.execute(stmt)
    return result.scalars().first()


@router.delete("/rooms/{room_id}", status_code=204)
async def delete_room(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    room = await verify_room_owner(room_id, user, db)
    await db.delete(room)
    await db.flush()


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
    return opening


@router.delete("/openings/{opening_id}", status_code=204)
async def delete_opening(
    opening_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opening = await verify_opening_owner(opening_id, user, db)
    await db.delete(opening)
    await db.flush()
