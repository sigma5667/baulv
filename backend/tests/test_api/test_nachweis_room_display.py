"""Nachweis-Zeilen zeigen Raumname + Wohnung (TOP) statt der Formel.

Live-Befund 2026-11-24 (Plan EP-05 OG): Spalte "Raum" zeigte
"Umfang × Höhe". Die API liefert jetzt ``room_name`` und ``unit_label``
je Zeile. ``unit_label`` ist nur gesetzt, wenn die Einheit eine echte
Wohnungsbezeichnung trägt — Platzhalter bleiben leer (nichts erfinden).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lv import get_lv
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.schemas.lv import LVResponse
from app.services.unit_label import room_display, unit_label


@pytest.mark.parametrize("name,expected", [
    ("TOP 5", "TOP 5"), ("Top 1.2", "Top 1.2"), ("top5", "top5"), ("WHG 7", "WHG 7"),
    ("W1", "W1"), ("Wohnung 3", "Wohnung 3"), ("  TOP   12  ", "TOP 12"),
    ("Einheit 1", None), ("Allgemein", None), ("OG", None), ("Stiegenhaus / Allgemein", None),
    ("", None), (None, None), ("Wohnen", None),
])
def test_unit_label_only_for_real_apartment_designations(name, expected):
    assert unit_label(name) == expected


def test_room_display():
    assert room_display("Bad", "TOP 5") == "TOP 5 · Bad"
    assert room_display("Bad", None) == "Bad"


async def test_lv_response_carries_room_name_and_top(db_session: AsyncSession):
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
    bad = Room(id=uuid.uuid4(), unit_id=top5.id, name="Bad", area_m2=Decimal("4.7"), is_active=True)
    gang = Room(id=uuid.uuid4(), unit_id=misc.id, name="Gang", area_m2=Decimal("6"), is_active=True)
    db_session.add_all([bad, gang])
    await db_session.flush()
    lv = Leistungsverzeichnis(id=uuid.uuid4(), project_id=project.id, name="LV", trade="malerarbeiten")
    db_session.add(lv)
    await db_session.flush()
    g = Leistungsgruppe(id=uuid.uuid4(), lv_id=lv.id, nummer="01", bezeichnung="Wand")
    db_session.add(g)
    await db_session.flush()
    pos = Position(id=uuid.uuid4(), gruppe_id=g.id, positions_nummer="01.01", kurztext="Wand", einheit="m2", menge=Decimal("1"))
    db_session.add(pos)
    await db_session.flush()
    for room, factor in ((bad, "1.0"), (gang, "1.5")):
        db_session.add(Berechnungsnachweis(
            id=uuid.uuid4(), position_id=pos.id, room_id=room.id, raw_quantity=Decimal("1"),
            formula_description="Umfang × Höhe", formula_expression="1 × 1",
            onorm_factor=Decimal(factor), deductions=[], net_quantity=Decimal("1"), unit="m2"))
    await db_session.commit()
    db_session.expunge_all()  # zwingt das echte Laden über die Endpoint-Query

    lv_orm = await get_lv(lv_id=lv.id, user=user, db=db_session)
    body = LVResponse.model_validate(lv_orm).model_dump(by_alias=True)
    rows = {r["room_name"]: r for r in body["gruppen"][0]["positionen"][0]["berechnungsnachweise"]}
    assert rows["Bad"]["unit_label"] == "TOP 5"
    assert rows["Gang"]["unit_label"] is None          # Platzhalter → leer, nichts erfunden
    assert rows["Gang"]["rule_factor"] == 1.5
