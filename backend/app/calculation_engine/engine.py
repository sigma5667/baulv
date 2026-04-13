"""Calculation engine orchestrator.

Loads room data, instantiates the correct trade calculator,
runs the deterministic calculation, and stores results.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.calculation_engine.registry import TradeRegistry
from app.calculation_engine.types import RoomWithOpenings, OpeningData, PositionQuantity
from app.db.models.project import Room, Opening, Unit, Floor, Building
from app.db.models.lv import Leistungsverzeichnis, Leistungsgruppe, Position
from app.db.models.calculation import Berechnungsnachweis

# Ensure trade modules are imported and registered
import app.calculation_engine.trades  # noqa: F401


async def load_rooms_for_project(project_id: UUID, db: AsyncSession) -> list[RoomWithOpenings]:
    """Load all rooms with openings for a project, building the hierarchy."""
    stmt = (
        select(Room)
        .join(Unit).join(Floor).join(Building)
        .where(Building.project_id == project_id)
        .options(selectinload(Room.openings), selectinload(Room.unit))
    )
    result = await db.execute(stmt)
    rooms = result.scalars().all()

    room_data: list[RoomWithOpenings] = []
    for room in rooms:
        openings = [
            OpeningData(
                opening_type=o.opening_type,
                width_m=Decimal(str(o.width_m)),
                height_m=Decimal(str(o.height_m)),
                count=o.count,
            )
            for o in room.openings
        ]

        room_data.append(RoomWithOpenings(
            id=room.id,
            name=room.name,
            room_type=room.room_type,
            area_m2=Decimal(str(room.area_m2 or 0)),
            perimeter_m=Decimal(str(room.perimeter_m or 0)),
            height_m=Decimal(str(room.height_m or "2.7")),
            floor_type=room.floor_type,
            wall_type=room.wall_type,
            ceiling_type=room.ceiling_type,
            is_wet_room=room.is_wet_room,
            has_dachschraege=room.has_dachschraege,
            is_staircase=room.is_staircase,
            unit_name=room.unit.name if room.unit else "",
            openings=openings,
        ))

    return room_data


async def calculate_lv(
    lv_id: UUID,
    db: AsyncSession,
) -> list[PositionQuantity]:
    """Run the calculation engine for an LV.

    1. Load the LV to get trade info
    2. Load all rooms for the project
    3. Instantiate the trade calculator
    4. Run deterministic calculation
    5. Store positions + Berechnungsnachweise in DB
    """
    # Load LV
    lv = await db.get(Leistungsverzeichnis, lv_id)
    if not lv:
        raise ValueError(f"LV {lv_id} not found")

    # Load rooms
    rooms = await load_rooms_for_project(lv.project_id, db)
    if not rooms:
        raise ValueError("No rooms found for this project. Please add rooms first.")

    # Get calculator
    calculator = TradeRegistry.get(lv.trade)

    # Run deterministic calculation
    results = calculator.calculate(rooms)

    # Clear existing positions and calculations for this LV
    for gruppe in lv.gruppen:
        await db.delete(gruppe)
    await db.flush()

    # Store results
    gruppen_map: dict[str, Leistungsgruppe] = {}

    for pos_qty in results:
        # Get or create Leistungsgruppe
        if pos_qty.gruppe_nummer not in gruppen_map:
            gruppe = Leistungsgruppe(
                lv_id=lv_id,
                nummer=pos_qty.gruppe_nummer,
                bezeichnung=pos_qty.gruppe_name,
                sort_order=int(pos_qty.gruppe_nummer),
            )
            db.add(gruppe)
            await db.flush()
            gruppen_map[pos_qty.gruppe_nummer] = gruppe

        gruppe = gruppen_map[pos_qty.gruppe_nummer]

        # Create Position
        position = Position(
            gruppe_id=gruppe.id,
            positions_nummer=pos_qty.position_code,
            kurztext=pos_qty.short_text,
            einheit=pos_qty.unit,
            menge=float(pos_qty.total_quantity),
            sort_order=int(pos_qty.position_code.replace(".", "")),
        )
        db.add(position)
        await db.flush()

        # Create Berechnungsnachweise
        for line in pos_qty.measurement_lines:
            nachweis = Berechnungsnachweis(
                position_id=position.id,
                room_id=UUID(line.room_id),
                raw_quantity=float(line.raw_quantity),
                formula_description=line.formula_description,
                formula_expression=line.formula_expression,
                onorm_factor=float(line.onorm_factor),
                onorm_rule_ref=line.onorm_rule_ref,
                onorm_paragraph=line.onorm_paragraph,
                deductions=[
                    {"opening": d.opening, "area": d.area, "deducted": d.deducted, "reason": d.reason}
                    for d in line.deductions
                ],
                net_quantity=float(line.net_quantity),
                unit=line.unit,
                notes=line.notes,
            )
            db.add(nachweis)

    await db.flush()
    return results
