"""Regression: after "Wandflächen übernehmen" the position's menge must be
exactly the sum of its Berechnungsnachweis rows.

Live incident (2026-11): the Malerarbeiten LV showed Wand 01.01 with
menge 1.071,27 m² while its Nachweis rows (left over from an earlier
"LV berechnen" run under a different opening rule) summed to
1.100,625 m². ``sync_wall_areas`` overwrote only ``menge``.

Fix: the sync replaces the rows too — one per contributing room, with the
room's cached net wall area — and sets menge = Σ rows. The opening rules
themselves are untouched; this test pins that the rows state the
Wandberechnung rule (single opening ≥ 2,5 m² deducted) they come from.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lv import sync_wall_areas
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.project import Building, Floor, Opening, Project, Room, Unit
from app.db.models.user import User


async def _seed(db: AsyncSession) -> dict:
    user = User(id=uuid.uuid4(), email=f"t-{uuid.uuid4()}@example.com",
                password_hash="x", full_name="T")
    db.add(user)
    await db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, name="Musterhaus")
    db.add(project)
    await db.flush()
    b = Building(id=uuid.uuid4(), project_id=project.id, name="Haus")
    db.add(b)
    await db.flush()
    f = Floor(id=uuid.uuid4(), building_id=b.id, name="EG")
    db.add(f)
    await db.flush()
    u = Unit(id=uuid.uuid4(), floor_id=f.id, name="Wohnung 1")
    db.add(u)
    await db.flush()

    # Wohnen: 23,00 m × 2,50 m = 57,50 m² brutto; Fenster 2,00×2,10 = 4,20 m²
    # (≥ 2,5 → abgezogen), Tür 0,90×2,00 = 1,80 m² (< 2,5 → übermessen).
    wohnen = Room(id=uuid.uuid4(), unit_id=u.id, name="Wohnen",
                  area_m2=Decimal("27.32"), perimeter_m=Decimal("23.00"),
                  height_m=Decimal("2.50"), wall_area_gross_m2=Decimal("57.50"),
                  wall_area_net_m2=Decimal("53.30"), applied_factor=Decimal("1.0"),
                  deductions_enabled=True, is_active=True)
    bad = Room(id=uuid.uuid4(), unit_id=u.id, name="Bad",
               area_m2=Decimal("4.79"), perimeter_m=Decimal("9.63"),
               height_m=Decimal("2.50"), wall_area_gross_m2=Decimal("24.08"),
               wall_area_net_m2=Decimal("24.08"), applied_factor=Decimal("1.0"),
               deductions_enabled=True, is_active=True)
    # Incomplete room: no cached wall area → not in the wall total, no row.
    leer = Room(id=uuid.uuid4(), unit_id=u.id, name="Abstellraum",
                area_m2=Decimal("2.22"), perimeter_m=None, height_m=Decimal("2.50"),
                wall_area_net_m2=None, is_active=True)
    db.add_all([wohnen, bad, leer])
    await db.flush()
    db.add_all([
        Opening(id=uuid.uuid4(), room_id=wohnen.id, opening_type="fenster",
                width_m=Decimal("2.00"), height_m=Decimal("2.10"), count=1),
        Opening(id=uuid.uuid4(), room_id=wohnen.id, opening_type="tuer",
                width_m=Decimal("0.90"), height_m=Decimal("2.00"), count=1),
        Opening(id=uuid.uuid4(), room_id=bad.id, opening_type="tuer",
                width_m=Decimal("0.80"), height_m=Decimal("2.00"), count=1),
    ])

    lv = Leistungsverzeichnis(id=uuid.uuid4(), project_id=project.id,
                              name="LV Malerarbeiten", trade="malerarbeiten")
    db.add(lv)
    await db.flush()
    g = Leistungsgruppe(id=uuid.uuid4(), lv_id=lv.id, nummer="01", bezeichnung="Malerarbeiten")
    db.add(g)
    await db.flush()
    wall = Position(id=uuid.uuid4(), gruppe_id=g.id, positions_nummer="01.01",
                    kurztext="Wandbeschichtung Dispersion weiß, 2× Anstrich",
                    einheit="m2", menge=Decimal("0"))
    ceiling = Position(id=uuid.uuid4(), gruppe_id=g.id, positions_nummer="02.01",
                       kurztext="Deckenbeschichtung Dispersion weiß, 2× Anstrich",
                       einheit="m2", menge=Decimal("0"))
    db.add_all([wall, ceiling])
    await db.flush()
    # A stale row from an earlier "LV berechnen" run (other rule, other value).
    db.add(Berechnungsnachweis(id=uuid.uuid4(), position_id=wall.id, room_id=wohnen.id,
                               raw_quantity=Decimal("57.5"), formula_description="Umfang × Höhe",
                               formula_expression="23.000 × 2.500", onorm_factor=Decimal("1"),
                               deductions=[], net_quantity=Decimal("57.5"), unit="m2"))
    await db.commit()
    return {"user": user, "lv": lv.id, "wall": wall.id, "ceiling": ceiling.id,
            "wohnen": wohnen.id, "bad": bad.id}


async def _rows(db: AsyncSession, pos_id) -> list[Berechnungsnachweis]:
    res = await db.execute(select(Berechnungsnachweis).where(Berechnungsnachweis.position_id == pos_id))
    return list(res.scalars().all())


async def test_menge_is_exact_sum_of_proof_rows(db_session: AsyncSession):
    s = await _seed(db_session)
    await sync_wall_areas(lv_id=s["lv"], user=s["user"], db=db_session)
    await db_session.commit()

    for key, expected in (("wall", Decimal("77.38")), ("ceiling", Decimal("34.33"))):
        pos = await db_session.get(Position, s[key])
        rows = await _rows(db_session, s[key])
        total = sum((Decimal(str(r.net_quantity)) for r in rows), Decimal("0"))
        assert Decimal(str(pos.menge)) == total, f"{key}: menge {pos.menge} != Σ Zeilen {total}"
        assert total == expected


async def test_stale_rows_are_replaced_one_row_per_contributing_room(db_session: AsyncSession):
    s = await _seed(db_session)
    await sync_wall_areas(lv_id=s["lv"], user=s["user"], db=db_session)
    await db_session.commit()

    rows = await _rows(db_session, s["wall"])
    assert {r.room_id for r in rows} == {s["wohnen"], s["bad"]}  # no incomplete room, no stale row
    wohnen = next(r for r in rows if r.room_id == s["wohnen"])
    assert Decimal(str(wohnen.raw_quantity)) == Decimal("57.50")
    assert Decimal(str(wohnen.net_quantity)) == Decimal("53.30")
    assert wohnen.onorm_rule_ref == "wandberechnung"
    assert "2,5 m²" in wohnen.onorm_paragraph and "ÖNORM" not in wohnen.onorm_paragraph
    by_type = {d["opening"].split()[0]: d for d in wohnen.deductions}
    assert by_type["fenster"]["deducted"] is True and by_type["fenster"]["area"] == 4.2
    assert by_type["tuer"]["deducted"] is False and by_type["tuer"]["area"] == 1.8
    assert wohnen.notes is None  # cached net is consistent with the openings


async def test_locked_position_keeps_menge_and_rows(db_session: AsyncSession):
    s = await _seed(db_session)
    wall = await db_session.get(Position, s["wall"])
    wall.is_locked = True
    wall.menge = Decimal("57.5")
    await db_session.commit()

    await sync_wall_areas(lv_id=s["lv"], user=s["user"], db=db_session)
    await db_session.commit()

    rows = await _rows(db_session, s["wall"])
    assert len(rows) == 1 and Decimal(str(rows[0].net_quantity)) == Decimal("57.5")
    assert Decimal(str((await db_session.get(Position, s["wall"])).menge)) == Decimal("57.5")
