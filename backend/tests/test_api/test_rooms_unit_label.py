"""Raumtabelle + Wandberechnung bekommen die Wohnung (TOP) je Raum.

Live-Befund 2026-11-24: bei Mehrparteien-Plänen hießen Räume nur "Bad",
"Wohnen", "Vorraum" — die Zuordnung zur Wohnung stand nirgends. Die
Raum-API liefert jetzt ``unit_name`` und ``unit_label``; letzteres nur
bei echter Wohnungsbezeichnung.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rooms import list_project_rooms
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.schemas.room import RoomResponse


async def test_room_list_carries_unit_label(db_session: AsyncSession):
    user = User(id=uuid.uuid4(), email=f"t-{uuid.uuid4()}@example.com", password_hash="x", full_name="T")
    db_session.add(user)
    await db_session.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Musterhaus")
    db_session.add(project)
    await db_session.flush()
    b = Building(id=uuid.uuid4(), project_id=project.id, name="Haus")
    db_session.add(b)
    await db_session.flush()
    f = Floor(id=uuid.uuid4(), building_id=b.id, name="OG")
    db_session.add(f)
    await db_session.flush()
    top5 = Unit(id=uuid.uuid4(), floor_id=f.id, name="TOP 5")
    misc = Unit(id=uuid.uuid4(), floor_id=f.id, name="Einheit 1")
    db_session.add_all([top5, misc])
    await db_session.flush()
    db_session.add_all([
        Room(id=uuid.uuid4(), unit_id=top5.id, name="Bad", area_m2=Decimal("4.7"), is_active=True),
        Room(id=uuid.uuid4(), unit_id=misc.id, name="Gang", area_m2=Decimal("6"), is_active=True),
    ])
    await db_session.commit()
    db_session.expunge_all()

    rooms = await list_project_rooms(project_id=project.id, plan_id=None, user=user, db=db_session)
    out = {r.name: RoomResponse.model_validate(r).model_dump() for r in rooms}
    assert out["Bad"]["unit_label"] == "TOP 5" and out["Bad"]["unit_name"] == "TOP 5"
    assert out["Gang"]["unit_label"] is None and out["Gang"]["unit_name"] == "Einheit 1"
