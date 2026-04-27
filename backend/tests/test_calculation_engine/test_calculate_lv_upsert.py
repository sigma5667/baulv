"""End-to-end tests for the v18 ``calculate_lv`` upsert behaviour.

The contract these tests pin down (see ``backend/app/calculation_engine/
engine.py`` module docstring for the full rationale):

1. First calculate on an empty LV creates groups + positions + BNs.
2. A second calculate that produces the same logical positions
   **does not change Position IDs** — agents and frontend bookmarks
   keep working across calculates.
3. ``langtext`` written by a user (``text_source = "manual"``)
   survives a subsequent calculate.
4. ``is_locked = True`` short-circuits the entire upsert for that
   position — ``kurztext`` / ``einheit`` / ``menge`` / ``einheitspreis``
   / BNs are all preserved, even when the calculator now produces
   completely different values.
5. When the new run produces fewer positions than exist, the
   no-longer-produced ones are deleted — **except** when
   ``is_locked = True``, in which case they survive.

We use a stub calculator so each test controls exactly what
``calculate_lv`` sees, free of Malerarbeiten-specific math.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.calculation_engine.engine import calculate_lv
from app.calculation_engine.types import (
    MeasurementLine,
    PositionQuantity,
)
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.lv import Leistungsgruppe, Position


def _line(room_id, qty: str = "10.0") -> MeasurementLine:
    """Build a minimal MeasurementLine for tests.

    The engine writes one ``Berechnungsnachweis`` per measurement line,
    so the count of these per ``PositionQuantity`` controls how many BNs
    end up in the DB — useful for the "BNs are replaced on update" check.
    """
    return MeasurementLine(
        room_id=str(room_id),
        room_name="Wohnzimmer",
        description="test",
        formula_description="P × H",
        formula_expression="18.0 × 2.7",
        raw_quantity=Decimal(qty),
        onorm_factor=Decimal("1.0"),
        onorm_rule_ref="B 2230-1",
        onorm_paragraph="§5.1",
        net_quantity=Decimal(qty),
        unit="m2",
    )


def _pq(
    gruppe_nummer: str,
    gruppe_name: str,
    position_code: str,
    short_text: str,
    unit: str,
    qty: str,
    room_id,
    lines: int = 1,
) -> PositionQuantity:
    """Build a PositionQuantity with N measurement lines."""
    return PositionQuantity(
        position_code=position_code,
        short_text=short_text,
        unit=unit,
        total_quantity=Decimal(qty),
        gruppe_nummer=gruppe_nummer,
        gruppe_name=gruppe_name,
        measurement_lines=[_line(room_id, qty) for _ in range(lines)],
    )


# ---------------------------------------------------------------------------
# 1. First calculate on empty LV
# ---------------------------------------------------------------------------


async def test_first_calculate_creates_groups_and_positions(
    db_session, seeded_lv, stub_calculator
):
    room_id = seeded_lv["room_id"]
    lv_id = seeded_lv["lv_id"]

    stub_calculator([
        _pq("01", "Vorarbeiten", "01.01", "Untergrund reinigen", "m2", "20.0", room_id),
        _pq("02", "Wandanstriche", "02.01", "Wandanstrich Dispersion", "m2", "48.6", room_id),
    ])

    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    gruppen = (
        await db_session.execute(
            select(Leistungsgruppe).where(Leistungsgruppe.lv_id == lv_id)
        )
    ).scalars().all()
    positions = (
        await db_session.execute(
            select(Position)
            .join(Leistungsgruppe)
            .where(Leistungsgruppe.lv_id == lv_id)
        )
    ).scalars().all()
    bns = (await db_session.execute(select(Berechnungsnachweis))).scalars().all()

    assert len(gruppen) == 2
    assert {g.nummer for g in gruppen} == {"01", "02"}
    assert len(positions) == 2
    assert {p.positions_nummer for p in positions} == {"01.01", "02.01"}
    assert {p.text_source for p in positions} == {"calculated"}
    assert len(bns) == 2  # one per position, one MeasurementLine each


# ---------------------------------------------------------------------------
# 2. Second calculate without changes keeps Position IDs
# ---------------------------------------------------------------------------


async def test_second_calculate_preserves_position_ids(
    db_session, seeded_lv, stub_calculator
):
    """The whole point of v18 — agents/UI references must survive a
    re-calculate when the calculator's view of the world hasn't changed.
    """
    room_id = seeded_lv["room_id"]
    lv_id = seeded_lv["lv_id"]

    results = [
        _pq("01", "Vorarbeiten", "01.01", "Untergrund", "m2", "20.0", room_id),
        _pq("02", "Wandanstriche", "02.01", "Wandanstrich", "m2", "48.6", room_id),
    ]

    stub_calculator(results)
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    ids_before = {
        p.positions_nummer: p.id
        for p in (
            await db_session.execute(
                select(Position)
                .join(Leistungsgruppe)
                .where(Leistungsgruppe.lv_id == lv_id)
            )
        ).scalars().all()
    }

    # Second run — same calculator output.
    stub_calculator(results)
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    ids_after = {
        p.positions_nummer: p.id
        for p in (
            await db_session.execute(
                select(Position)
                .join(Leistungsgruppe)
                .where(Leistungsgruppe.lv_id == lv_id)
            )
        ).scalars().all()
    }

    assert ids_before == ids_after, (
        "Position IDs rotated across calculate runs — the v17 bug is back."
    )


# ---------------------------------------------------------------------------
# 3. Manual edit (langtext, text_source="manual") is preserved
# ---------------------------------------------------------------------------


async def test_manual_langtext_survives_calculate(
    db_session, seeded_lv, stub_calculator
):
    room_id = seeded_lv["room_id"]
    lv_id = seeded_lv["lv_id"]

    stub_calculator([
        _pq("01", "Vorarbeiten", "01.01", "Untergrund", "m2", "20.0", room_id),
    ])
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    # User edits the langtext manually and flips text_source.
    pos = (
        await db_session.execute(
            select(Position)
            .join(Leistungsgruppe)
            .where(Leistungsgruppe.lv_id == lv_id)
        )
    ).scalars().first()
    pos.langtext = "USER WROTE THIS — must not be overwritten"
    pos.text_source = "manual"
    pos.einheitspreis = 12.50
    await db_session.commit()
    pos_id = pos.id

    # Re-calculate. The calculator output for this position changes
    # (different kurztext, different qty) — those calculator-owned
    # fields should update, but langtext / text_source / einheitspreis
    # must not.
    stub_calculator([
        _pq("01", "Vorarbeiten", "01.01", "Untergrund REINIGEN STARK", "m2", "25.0", room_id),
    ])
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    pos = (
        await db_session.execute(select(Position).where(Position.id == pos_id))
    ).scalars().first()

    assert pos is not None, "Position was deleted across re-calculate"
    assert pos.id == pos_id  # identity unchanged
    assert pos.langtext == "USER WROTE THIS — must not be overwritten"
    assert pos.text_source == "manual"
    assert float(pos.einheitspreis) == 12.50
    # Calculator-owned fields *did* update:
    assert pos.kurztext == "Untergrund REINIGEN STARK"
    assert float(pos.menge) == 25.0


# ---------------------------------------------------------------------------
# 4. is_locked = True short-circuits everything
# ---------------------------------------------------------------------------


async def test_is_locked_position_is_fully_protected(
    db_session, seeded_lv, stub_calculator
):
    room_id = seeded_lv["room_id"]
    lv_id = seeded_lv["lv_id"]

    stub_calculator([
        _pq("01", "Vorarbeiten", "01.01", "Untergrund", "m2", "20.0", room_id),
    ])
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    pos = (
        await db_session.execute(
            select(Position)
            .join(Leistungsgruppe)
            .where(Leistungsgruppe.lv_id == lv_id)
        )
    ).scalars().first()
    pos.is_locked = True
    pos.kurztext = "LOCKED — original kurztext"
    pos.einheit = "Stk"
    pos.menge = 99.0
    pos.einheitspreis = 50.0
    pos.langtext = "Locked langtext"
    await db_session.commit()
    pos_id = pos.id

    # Calculator now wants something completely different. None of it
    # should land in the DB because the position is locked.
    stub_calculator([
        _pq("01", "Vorarbeiten", "01.01", "Calculator wants this kurztext", "m2", "999.9", room_id, lines=3),
    ])
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    pos = (
        await db_session.execute(select(Position).where(Position.id == pos_id))
    ).scalars().first()

    assert pos is not None
    assert pos.is_locked is True
    assert pos.kurztext == "LOCKED — original kurztext"
    assert pos.einheit == "Stk"
    assert float(pos.menge) == 99.0
    assert float(pos.einheitspreis) == 50.0
    assert pos.langtext == "Locked langtext"

    # And the BNs were not touched either — there were 0 BNs originally
    # (locked-update path doesn't touch them; locked-orphan doesn't
    # either). The new run wanted 3 BNs but mustn't have written any.
    bn_count = (
        await db_session.execute(
            select(Berechnungsnachweis).where(
                Berechnungsnachweis.position_id == pos_id
            )
        )
    ).scalars().all()
    # Originally seeded by the first calculate (1 measurement line),
    # the locked re-run must not have replaced or augmented them.
    assert len(bn_count) == 1


# ---------------------------------------------------------------------------
# 5. Fewer positions on second run → unlocked deleted, locked survive
# ---------------------------------------------------------------------------


async def test_orphan_cleanup_respects_lock(
    db_session, seeded_lv, stub_calculator
):
    room_id = seeded_lv["room_id"]
    lv_id = seeded_lv["lv_id"]

    # Initial run: three positions in two groups.
    stub_calculator([
        _pq("01", "Vorarbeiten", "01.01", "A", "m2", "10.0", room_id),
        _pq("01", "Vorarbeiten", "01.02", "B", "m2", "10.0", room_id),
        _pq("02", "Wandanstriche", "02.01", "C", "m2", "10.0", room_id),
    ])
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    # Lock 01.02 — user wants this kept regardless of what the
    # calculator says next.
    locked_pos = (
        await db_session.execute(
            select(Position).where(Position.positions_nummer == "01.02")
        )
    ).scalars().first()
    locked_pos.is_locked = True
    locked_pos_id = locked_pos.id
    await db_session.commit()

    # New run drops 01.02 and 02.01 entirely; only 01.01 remains.
    stub_calculator([
        _pq("01", "Vorarbeiten", "01.01", "A", "m2", "10.0", room_id),
    ])
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    surviving = {
        p.positions_nummer: p.id
        for p in (
            await db_session.execute(
                select(Position)
                .join(Leistungsgruppe)
                .where(Leistungsgruppe.lv_id == lv_id)
            )
        ).scalars().all()
    }

    # 01.01 (re-produced) and 01.02 (locked) survive. 02.01 was
    # unlocked + not re-produced → deleted.
    assert set(surviving.keys()) == {"01.01", "01.02"}
    assert surviving["01.02"] == locked_pos_id

    # Group "02" had only the (now-deleted) 02.01 — empty-group
    # cleanup should have evicted it.
    gruppen_nummern = {
        g.nummer
        for g in (
            await db_session.execute(
                select(Leistungsgruppe).where(Leistungsgruppe.lv_id == lv_id)
            )
        ).scalars().all()
    }
    assert gruppen_nummern == {"01"}


# ---------------------------------------------------------------------------
# Bonus assertion: locked position holds an otherwise-empty group alive.
# ---------------------------------------------------------------------------
#
# This isn't one of the five requested cases but it's a critical edge of
# the lock semantics — if we deleted the group around a locked position
# the position would dangle. Cheap to verify, expensive to debug if we
# break it later.


async def test_locked_position_holds_group_alive(
    db_session, seeded_lv, stub_calculator
):
    room_id = seeded_lv["room_id"]
    lv_id = seeded_lv["lv_id"]

    stub_calculator([
        _pq("99", "Sonderarbeiten", "99.01", "Lonely position", "m2", "5.0", room_id),
    ])
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    pos = (
        await db_session.execute(
            select(Position).where(Position.positions_nummer == "99.01")
        )
    ).scalars().first()
    pos.is_locked = True
    await db_session.commit()

    # New run produces nothing for group 99. The locked position should
    # survive, and so should its group.
    stub_calculator([])
    # Empty calculator output triggers no upsert work but cleanup must
    # still run. Note: ``calculate_lv`` doesn't reject empty calculator
    # output — only an empty *room* set is fatal.
    await calculate_lv(lv_id, db_session)
    await db_session.commit()

    surviving_groups = {
        g.nummer
        for g in (
            await db_session.execute(
                select(Leistungsgruppe).where(Leistungsgruppe.lv_id == lv_id)
            )
        ).scalars().all()
    }
    assert "99" in surviving_groups, (
        "Group of a locked position was evicted — dangling lock."
    )

    surviving_positions = (
        await db_session.execute(
            select(Position).where(Position.positions_nummer == "99.01")
        )
    ).scalars().all()
    assert len(surviving_positions) == 1
