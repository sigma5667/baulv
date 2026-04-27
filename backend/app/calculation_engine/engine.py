"""Calculation engine orchestrator.

Loads room data, instantiates the correct trade calculator,
runs the deterministic calculation, and persists the result as an
**upsert** against the existing LV — preserving identity (and any
manual edits) of positions across repeated calculate runs.

History
=======

Up to v17 this module worked by deleting **every** ``Leistungsgruppe``
of the LV (which CASCADEd to every ``Position`` and every
``Berechnungsnachweis``) and recreating the lot from the calculator's
output. Two consequences:

* Position IDs rotated on every ``/calculate``. Agents (or anything
  that holds a position reference across runs) saw their IDs evaporate.
* ``is_locked = True``, ``langtext`` (manual edits) and
  ``einheitspreis`` were silently lost on every calculate, despite
  the lock-button promise that "this position is protected".

v18 fixes both by upserting:

* Existing groups are matched by ``(lv_id, nummer)`` and only their
  ``bezeichnung`` / ``sort_order`` are refreshed.
* Existing positions are matched by ``(gruppe_id, positions_nummer)``
  and only the **calculator-owned** fields (``kurztext``, ``einheit``,
  ``menge``, ``sort_order``) are updated.
* User-owned fields (``langtext``, ``einheitspreis``, ``is_locked``,
  ``text_source``) are never touched on update.
* ``is_locked = True`` short-circuits the entire update — the position
  keeps every field it had, BNs included, and survives the cleanup
  phase even when the new run no longer produces it.
* Positions the new run no longer produces are deleted (CASCADE on
  BNs) — but only when ``is_locked = False``.
* Groups left empty after position cleanup are deleted.

Berechnungsnachweise are still **replaced** per (unlocked) position.
BN IDs are intentionally not stable across runs — they're pure proof
of derivation, not addressable entities. Agents reference positions,
not BNs. See ``docs/ID_MIGRATION.md`` for the full reasoning.
"""

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.calculation_engine.registry import TradeRegistry
from app.calculation_engine.types import (
    OpeningData,
    PositionQuantity,
    RoomWithOpenings,
)
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.project import Building, Floor, Room, Unit

# Ensure trade modules are imported and registered
import app.calculation_engine.trades  # noqa: F401

logger = logging.getLogger(__name__)


def _code_to_sort_order(code: str) -> int:
    """Pack a hierarchical code like ``'01.02.003'`` into a sortable int.

    Each dot-separated segment is treated as a base-1000 digit:

    * ``'01.01.001'`` →   1_001_001
    * ``'01.01.010'`` →   1_001_010
    * ``'01.02.001'`` →   1_002_001
    * ``'02'``        →   2

    Compared to ``int(code.replace('.', ''))`` this preserves correct
    ordering when segments have differing widths — the previous approach
    accidentally sorted ``'01.10.1'`` before ``'01.2.1'`` (because
    ``'01101'`` < ``'01210'`` lexically but as an int the carry breaks the
    hierarchy entirely once any segment hits two digits).

    Non-numeric segments fall back to 0 so this never raises. Each segment
    is clamped to [0, 999] so a malformed code can't bleed into higher
    positions and collide with unrelated codes.
    """
    result = 0
    for part in code.split("."):
        part = part.strip()
        try:
            n = int(part)
        except ValueError:
            n = 0
        if n < 0:
            n = 0
        elif n > 999:
            n = 999
        result = result * 1000 + n
    return result


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


def _replace_berechnungsnachweise(
    db: AsyncSession,
    pos: Position,
    measurement_lines: list,
) -> None:
    """Replace all Berechnungsnachweise of an unlocked position.

    Removes the existing BNs via the relationship so SQLAlchemy's
    ``delete-orphan`` cascade evicts them on the next flush, then adds
    fresh rows for every ``MeasurementLine`` in the calculator's output.

    BN IDs are deliberately not preserved — see the module docstring.
    """
    for bn in list(pos.berechnungsnachweise):
        pos.berechnungsnachweise.remove(bn)
    for line in measurement_lines:
        nachweis = Berechnungsnachweis(
            position_id=pos.id,
            room_id=UUID(line.room_id),
            raw_quantity=float(line.raw_quantity),
            formula_description=line.formula_description,
            formula_expression=line.formula_expression,
            onorm_factor=float(line.onorm_factor),
            onorm_rule_ref=line.onorm_rule_ref,
            onorm_paragraph=line.onorm_paragraph,
            deductions=[
                {
                    "opening": d.opening,
                    "area": d.area,
                    "deducted": d.deducted,
                    "reason": d.reason,
                }
                for d in line.deductions
            ],
            net_quantity=float(line.net_quantity),
            unit=line.unit,
            notes=line.notes,
        )
        db.add(nachweis)


async def calculate_lv(
    lv_id: UUID,
    db: AsyncSession,
) -> list[PositionQuantity]:
    """Run the calculation engine for an LV and upsert the result.

    See module docstring for the full upsert contract. Returns the raw
    ``PositionQuantity`` list from the calculator so the API layer can
    report counts for the response payload.
    """
    # Eager-load the whole tree — we need positions and their BNs to
    # decide what to update vs. replace vs. delete.
    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen)
            .selectinload(Leistungsgruppe.positionen)
            .selectinload(Position.berechnungsnachweise),
        )
    )
    result = await db.execute(stmt)
    lv = result.scalars().first()
    if not lv:
        raise ValueError(f"LV {lv_id} not found")

    rooms = await load_rooms_for_project(lv.project_id, db)
    if not rooms:
        raise ValueError(
            "Keine Räume für dieses Projekt gefunden. "
            "Bitte laden Sie zuerst einen Bauplan hoch und analysieren Sie ihn, "
            "oder erstellen Sie Räume manuell über Gebäude → Stockwerk → Einheit → Raum."
        )

    calculator = TradeRegistry.get(lv.trade)
    results = calculator.calculate(rooms)

    # Index existing structure by stable secondary keys for O(1) lookup.
    # ``existing_gruppen`` keys on ``nummer`` (unique within an LV).
    # ``existing_positions`` keys on ``(gruppe_nummer, positions_nummer)``
    # so a position's identity is independent of its DB ``gruppe_id``
    # — that matters when a group is deleted and recreated, even though
    # in v18+ that should never happen for matched groups.
    existing_gruppen: dict[str, Leistungsgruppe] = {
        g.nummer: g for g in lv.gruppen
    }
    existing_positions: dict[tuple[str, str], Position] = {}
    for g in lv.gruppen:
        for p in g.positionen:
            existing_positions[(g.nummer, p.positions_nummer)] = p

    # Counters for the structured log line emitted at the end. Easier to
    # grep than scattered "+1" lines per branch.
    gruppen_created = 0
    gruppen_updated = 0
    positions_created = 0
    positions_updated = 0
    positions_locked_skipped = 0

    # Track what the new run produced so the cleanup phase knows what to
    # leave alone vs. delete vs. protect-because-locked.
    seen_position_keys: set[tuple[str, str]] = set()

    for pos_qty in results:
        # ---- Group upsert -------------------------------------------
        gruppe = existing_gruppen.get(pos_qty.gruppe_nummer)
        if gruppe is None:
            gruppe = Leistungsgruppe(
                lv_id=lv_id,
                nummer=pos_qty.gruppe_nummer,
                bezeichnung=pos_qty.gruppe_name,
                sort_order=_code_to_sort_order(pos_qty.gruppe_nummer),
            )
            db.add(gruppe)
            await db.flush()
            existing_gruppen[pos_qty.gruppe_nummer] = gruppe
            gruppen_created += 1
        else:
            # The group itself has no user-owned fields, so refreshing
            # bezeichnung / sort_order is safe.
            gruppe.bezeichnung = pos_qty.gruppe_name
            gruppe.sort_order = _code_to_sort_order(pos_qty.gruppe_nummer)
            gruppen_updated += 1

        # ---- Position upsert ----------------------------------------
        key = (pos_qty.gruppe_nummer, pos_qty.position_code)
        seen_position_keys.add(key)
        pos = existing_positions.get(key)

        if pos is None:
            # Brand new position — fully driven by the calculator.
            pos = Position(
                gruppe_id=gruppe.id,
                positions_nummer=pos_qty.position_code,
                kurztext=pos_qty.short_text,
                einheit=pos_qty.unit,
                menge=float(pos_qty.total_quantity),
                sort_order=_code_to_sort_order(pos_qty.position_code),
                text_source="calculated",
            )
            db.add(pos)
            await db.flush()
            existing_positions[key] = pos
            positions_created += 1
            _replace_berechnungsnachweise(db, pos, pos_qty.measurement_lines)
            continue

        if pos.is_locked:
            # Hard short-circuit: locked = nothing changes.
            # Not even the BNs — the BNs back the *current* menge, and
            # the menge is locked.
            positions_locked_skipped += 1
            logger.info(
                "calculate.skip_locked lv_id=%s position_id=%s "
                "positions_nummer=%s reason=upsert",
                lv_id, pos.id, pos.positions_nummer,
            )
            continue

        # Existing, unlocked position — refresh calculator-owned fields
        # only. ``langtext`` / ``einheitspreis`` / ``is_locked`` /
        # ``text_source`` survive the calculate so users (and agents)
        # don't lose edits.
        pos.kurztext = pos_qty.short_text
        pos.einheit = pos_qty.unit
        pos.menge = float(pos_qty.total_quantity)
        pos.sort_order = _code_to_sort_order(pos_qty.position_code)
        positions_updated += 1
        _replace_berechnungsnachweise(db, pos, pos_qty.measurement_lines)

    await db.flush()

    # ---- Cleanup: positions the new run no longer produces ----------
    positions_deleted = 0
    for key, pos in list(existing_positions.items()):
        if key in seen_position_keys:
            continue
        if pos.is_locked:
            # Keep — user explicitly locked it; an empty calculator run
            # shouldn't be enough to evict it.
            positions_locked_skipped += 1
            logger.info(
                "calculate.skip_locked lv_id=%s position_id=%s "
                "positions_nummer=%s reason=orphan",
                lv_id, pos.id, pos.positions_nummer,
            )
            continue
        await db.delete(pos)
        positions_deleted += 1
    await db.flush()

    # ---- Cleanup: groups that are now empty -------------------------
    # A group can end up empty when the new run dropped all of its
    # positions and none of them were locked. Locked positions held
    # their group alive — that's the right behaviour, leaving the
    # locked position dangling under no group would be worse.
    gruppen_deleted = 0
    empty_gruppen_stmt = (
        select(Leistungsgruppe)
        .outerjoin(Position, Position.gruppe_id == Leistungsgruppe.id)
        .where(Leistungsgruppe.lv_id == lv_id)
        .group_by(Leistungsgruppe.id)
        .having(func.count(Position.id) == 0)
    )
    empty_gruppen = (await db.execute(empty_gruppen_stmt)).scalars().all()
    for g in empty_gruppen:
        await db.delete(g)
        gruppen_deleted += 1
    await db.flush()

    logger.info(
        "calculate.upsert lv_id=%s gruppen_created=%d gruppen_updated=%d "
        "gruppen_deleted=%d positions_created=%d positions_updated=%d "
        "positions_deleted=%d positions_locked_skipped=%d",
        lv_id, gruppen_created, gruppen_updated, gruppen_deleted,
        positions_created, positions_updated, positions_deleted,
        positions_locked_skipped,
    )

    return results
