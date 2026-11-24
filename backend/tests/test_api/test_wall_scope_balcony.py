"""Balkone/Terrassen/Loggien: nicht in der Tabelle-Summe, nicht im LV.

Entscheidung des Betreibers 2026-11-24 (Punkt A): EINE Ausschluss-Regel
für Wandberechnungs-Tabelle, "Wandflächen übernehmen" und "LV berechnen"
(``app/services/wall_scope.py``). Geprüft mit einem Balkon im Projekt:

* "Wandflächen übernehmen": Summe Tabelle = LV-Menge = Σ Nachweis-Zeilen.
* "LV berechnen": Balkon fehlt in den Zeilen, LV-Menge = Σ Nachweis-
  Zeilen. (Die Zahl selbst weicht von der Tabelle ab, solange die beiden
  Abzugsregeln 2,5 / 5,0 m² nicht vereinheitlicht sind — offene Fachfrage.)
* Die Schlagwortliste im Frontend ist zeichengleich mit dem Backend.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lv import sync_wall_areas
from app.calculation_engine.engine import calculate_lv
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.services.wall_scope import (
    AUSSENFLAECHE_KEYWORDS,
    AUSSENFLAECHE_TYPES,
    counts_for_wall_sum,
    is_aussenflaeche,
)


async def _seed(db: AsyncSession, *, with_positions: bool) -> dict:
    user = User(id=uuid.uuid4(), email=f"t-{uuid.uuid4()}@example.com", password_hash="x", full_name="T")
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Musterhaus")
    db.add(project)
    await db.flush()
    b = Building(id=uuid.uuid4(), project_id=project.id, name="Haus")
    db.add(b)
    await db.flush()
    f = Floor(id=uuid.uuid4(), building_id=b.id, name="OG")
    db.add(f)
    await db.flush()
    u = Unit(id=uuid.uuid4(), floor_id=f.id, name="TOP 5")
    db.add(u)
    await db.flush()

    def room(name, rtype, area, peri, net, active=True):
        return Room(id=uuid.uuid4(), unit_id=u.id, name=name, room_type=rtype,
                    area_m2=Decimal(area), perimeter_m=Decimal(peri), height_m=Decimal("2.50"),
                    wall_area_gross_m2=Decimal(net), wall_area_net_m2=Decimal(net),
                    applied_factor=Decimal("1.0"), deductions_enabled=True, is_active=active)

    rooms = [
        room("Wohnen", "wohnzimmer", "27.32", "23.00", "57.50"),
        room("Bad", "bad", "4.79", "9.63", "24.08"),
        room("Balkon TOP 5", "balkon", "8.20", "16.62", "41.55"),       # Typ balkon
        room("Loggia", "sonstiges", "6.00", "10.00", "25.00"),           # nur über den Namen
        room("Abstellraum", "abstellraum", "2.22", "6.56", "16.40", active=False),
    ]
    db.add_all(rooms)
    await db.flush()
    lv = Leistungsverzeichnis(id=uuid.uuid4(), project_id=project.id, name="LV", trade="malerarbeiten")
    db.add(lv)
    await db.flush()
    if with_positions:
        g = Leistungsgruppe(id=uuid.uuid4(), lv_id=lv.id, nummer="01", bezeichnung="Wand")
        db.add(g)
        await db.flush()
        db.add(Position(id=uuid.uuid4(), gruppe_id=g.id, positions_nummer="01.01",
                        kurztext="Wandbeschichtung Dispersion weiß, 2× Anstrich", einheit="m2",
                        menge=Decimal("0")))
    await db.commit()
    return {"user": user, "lv": lv.id, "rooms": rooms}


async def _wall_position(db: AsyncSession, lv_id) -> tuple[Position, list[Berechnungsnachweis]]:
    pos = (await db.execute(
        select(Position).join(Leistungsgruppe).where(
            Leistungsgruppe.lv_id == lv_id, Position.positions_nummer == "01.01")
    )).scalars().one()
    rows = list((await db.execute(
        select(Berechnungsnachweis).where(Berechnungsnachweis.position_id == pos.id)
    )).scalars().all())
    return pos, rows


async def test_sync_table_sum_equals_lv_menge_equals_proof_rows(db_session: AsyncSession):
    s = await _seed(db_session, with_positions=True)
    table_sum = sum((Decimal(str(r.wall_area_net_m2)) for r in s["rooms"] if counts_for_wall_sum(r)), Decimal(0))
    assert table_sum == Decimal("81.58")  # Wohnen + Bad; Balkon, Loggia, inaktiv nicht

    await sync_wall_areas(lv_id=s["lv"], user=s["user"], db=db_session)
    await db_session.commit()
    pos, rows = await _wall_position(db_session, s["lv"])
    rows_sum = sum((Decimal(str(r.net_quantity)) for r in rows), Decimal(0))
    assert Decimal(str(pos.menge)) == table_sum == rows_sum
    balkon_ids = {r.id for r in s["rooms"] if is_aussenflaeche(r.name, r.room_type)}
    assert not balkon_ids & {r.room_id for r in rows}


async def test_lv_berechnen_excludes_balcony_and_menge_equals_rows(db_session: AsyncSession):
    s = await _seed(db_session, with_positions=False)
    await calculate_lv(s["lv"], db_session)
    await db_session.commit()
    pos, rows = await _wall_position(db_session, s["lv"])
    names = {r.id: r.name for r in s["rooms"]}
    assert sorted(names[r.room_id] for r in rows) == ["Bad", "Wohnen"]
    assert Decimal(str(pos.menge)) == sum((Decimal(str(r.net_quantity)) for r in rows), Decimal(0))


def test_frontend_uses_the_same_exclusion_list():
    tsx = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages" / "PlanAnalysisPage.tsx").read_text(encoding="utf-8")
    kw = re.search(r"const _AUSSENFLAECHE_KEYWORDS = \[([^\]]*)\]", tsx).group(1)
    assert tuple(re.findall(r'"([^"]+)"', kw)) == AUSSENFLAECHE_KEYWORDS
    assert 'type === "balkon" || type === "terrasse"' in tsx
    assert AUSSENFLAECHE_TYPES == ("balkon", "terrasse")
