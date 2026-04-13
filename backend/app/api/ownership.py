"""Ownership verification helpers for multi-tenant security."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.project import Project, Building, Floor, Unit, Room, Opening
from app.db.models.plan import Plan
from app.db.models.lv import Leistungsverzeichnis
from app.db.models.chat import ChatSession
from app.db.models.user import User


async def verify_project_owner(
    project_id: UUID, user: User, db: AsyncSession
) -> Project:
    """Verify that the project exists and belongs to the user. Returns the project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Projekt nicht gefunden")
    if project.user_id != user.id:
        raise HTTPException(403, "Zugriff verweigert")
    return project


async def verify_plan_owner(
    plan_id: UUID, user: User, db: AsyncSession
) -> Plan:
    """Verify that the plan's project belongs to the user."""
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan nicht gefunden")
    await verify_project_owner(plan.project_id, user, db)
    return plan


async def verify_building_owner(
    building_id: UUID, user: User, db: AsyncSession
) -> Building:
    """Verify that the building's project belongs to the user."""
    building = await db.get(Building, building_id)
    if not building:
        raise HTTPException(404, "Gebaeude nicht gefunden")
    await verify_project_owner(building.project_id, user, db)
    return building


async def verify_floor_owner(
    floor_id: UUID, user: User, db: AsyncSession
) -> Floor:
    """Verify that the floor's building's project belongs to the user."""
    floor = await db.get(Floor, floor_id)
    if not floor:
        raise HTTPException(404, "Stockwerk nicht gefunden")
    await verify_building_owner(floor.building_id, user, db)
    return floor


async def verify_unit_owner(
    unit_id: UUID, user: User, db: AsyncSession
) -> Unit:
    """Verify that the unit's floor's building's project belongs to the user."""
    unit = await db.get(Unit, unit_id)
    if not unit:
        raise HTTPException(404, "Einheit nicht gefunden")
    await verify_floor_owner(unit.floor_id, user, db)
    return unit


async def verify_room_owner(
    room_id: UUID, user: User, db: AsyncSession
) -> Room:
    """Verify that the room's unit chain belongs to the user."""
    room = await db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "Raum nicht gefunden")
    await verify_unit_owner(room.unit_id, user, db)
    return room


async def verify_opening_owner(
    opening_id: UUID, user: User, db: AsyncSession
) -> Opening:
    """Verify that the opening's room chain belongs to the user."""
    opening = await db.get(Opening, opening_id)
    if not opening:
        raise HTTPException(404, "Oeffnung nicht gefunden")
    await verify_room_owner(opening.room_id, user, db)
    return opening


async def verify_lv_owner(
    lv_id: UUID, user: User, db: AsyncSession
) -> Leistungsverzeichnis:
    """Verify that the LV's project belongs to the user."""
    lv = await db.get(Leistungsverzeichnis, lv_id)
    if not lv:
        raise HTTPException(404, "LV nicht gefunden")
    await verify_project_owner(lv.project_id, user, db)
    return lv


async def verify_chat_session_owner(
    session_id: UUID, user: User, db: AsyncSession
) -> ChatSession:
    """Verify that the chat session's project belongs to the user."""
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(404, "Chat-Session nicht gefunden")
    if session.project_id:
        await verify_project_owner(session.project_id, user, db)
    # Sessions without a project_id are global — allow if user is authenticated
    return session
