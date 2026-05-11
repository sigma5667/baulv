"""Tests for the v24.3.1 default-height writeback in the KI pipeline.

Background
==========

Pre-v24.3.1 the pipeline persisted rooms with ``height_m=None`` when
Vision didn't extract a height (typical on Grundriss-only uploads —
heights live in Schnitt-Pläne). The wall-calc cache was still
computed against the 2,50 m fallback, but the DB itself carried a
NULL height_m. That created two visible bugs downstream:

  * The Mengenermittlungs-PDF renders ``room.height_m`` directly — a
    NULL renders as the em-dash placeholder, the Wandflächen-brutto
    formula in the detail block silently skipped.
  * The frontend's wall-calc table had a display-override that
    showed "2,50" for the NULL-height rows. Combined with the
    InlineNumericEdit's "same value → skip" guard, a user who
    confirmed 2,50 by typing it back into the cell got no save
    fired — the row stayed NULL in the DB.

v24.3.1 fixes the pipeline so it mirrors the writeback that
``_recalculate_walls_and_persist`` (in ``rooms.py``) has had
since v22.2: if ``height_m`` is None after the calc, write the
resolved default back so the DB is internally consistent.

This test file locks the contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.plan import Plan
from app.db.models.project import Project, Room
from app.db.models.user import User
from app.plan_analysis.pipeline import _store_extraction_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_plan(db: AsyncSession) -> Plan:
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db.add(user)
    await db.flush()

    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db.add(project)
    await db.flush()

    plan = Plan(
        id=uuid.uuid4(),
        project_id=project.id,
        filename="grundriss.pdf",
        file_path="/tmp/grundriss.pdf",
        analysis_status="processing",
        created_at=datetime.now(timezone.utc),
    )
    db.add(plan)
    await db.flush()
    return plan


def _vision_result_without_height() -> dict:
    """A typical Grundriss-only Vision result: rooms have area +
    perimeter but no ``height_m`` (because the height isn't readable
    from a floor-plan view — that's what Schnitt-Pläne are for)."""
    return {
        "floor_name": "EG",
        "floor_level": 0,
        "units": [
            {
                "unit_name": "Top 1",
                "unit_type": "wohnung",
                "rooms": [
                    {
                        "room_name": "Wohnzimmer",
                        "area_m2": 24.5,
                        "perimeter_m": 20.0,
                        # height_m intentionally omitted.
                    },
                    {
                        "room_name": "Schlafzimmer",
                        "area_m2": 16.0,
                        "perimeter_m": 16.5,
                        # height_m intentionally omitted.
                    },
                ],
            }
        ],
    }


def _vision_result_with_height(height: float) -> dict:
    """Same shape, but with an explicit Vision-extracted height.
    Used to verify the writeback doesn't trample real values."""
    return {
        "floor_name": "EG",
        "floor_level": 0,
        "units": [
            {
                "unit_name": "Top 1",
                "unit_type": "wohnung",
                "rooms": [
                    {
                        "room_name": "Wohnzimmer",
                        "area_m2": 24.5,
                        "perimeter_m": 20.0,
                        "height_m": height,
                        "ceiling_height_source": "grundriss",
                    },
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_writes_default_height_when_vision_omits_it(
    db_session: AsyncSession,
):
    """Vision returned no height_m → pipeline must persist 2.50 m
    (with ``ceiling_height_source="default"``) instead of leaving
    height_m NULL.

    This is the core fix for the Vater-Bug: pre-v24.3.1 NULL stayed
    and the PDF rendered em-dashes for the height column."""
    plan = await _seed_plan(db_session)

    rooms_created = await _store_extraction_result(
        _vision_result_without_height(), plan, db_session
    )
    await db_session.commit()

    assert rooms_created == 2

    rooms = (
        await db_session.execute(select(Room).order_by(Room.name))
    ).scalars().all()
    assert len(rooms) == 2

    # The headline assertion: every room's height_m is populated,
    # not NULL. The wall-calc cache and the PDF both read this
    # column directly.
    for r in rooms:
        assert r.height_m is not None, (
            f"Room {r.name!r} kept NULL height_m after pipeline — "
            f"v24.3.1 writeback regressed."
        )
        # The resolved fallback is the 2,50 m residential default.
        assert abs(float(r.height_m) - 2.5) < 1e-6
        # Source flag stays "default" so the UI can still flag the
        # row as a placeholder the user should confirm.
        assert r.ceiling_height_source == "default"


@pytest.mark.asyncio
async def test_pipeline_preserves_explicit_height_from_vision(
    db_session: AsyncSession,
):
    """When Vision DID find a height (e.g. via a labeled "RH 2,40"
    annotation on the floor plan), the writeback must NOT overwrite
    it. The writeback is a NULL-only fallback, not a normaliser."""
    plan = await _seed_plan(db_session)

    rooms_created = await _store_extraction_result(
        _vision_result_with_height(2.40), plan, db_session
    )
    await db_session.commit()

    assert rooms_created == 1
    room = (
        await db_session.execute(select(Room))
    ).scalars().first()
    assert room is not None
    assert abs(float(room.height_m) - 2.40) < 1e-6
    # Source flag carries Vision's claim that this came from the
    # Grundriss (one of the four accepted values).
    assert room.ceiling_height_source == "grundriss"


@pytest.mark.asyncio
async def test_pipeline_height_zero_treated_as_missing(
    db_session: AsyncSession,
):
    """Vision sometimes returns ``height_m: 0`` (or 0.0) when it
    can't read the value. The pipeline's existing logic flips
    such values to "default"; the new writeback then fills 2.50.

    Locks both halves of that behaviour in one test."""
    plan = await _seed_plan(db_session)

    result = {
        "floor_name": "EG",
        "floor_level": 0,
        "units": [
            {
                "unit_name": "Top 1",
                "unit_type": "wohnung",
                "rooms": [
                    {
                        "room_name": "Wohnzimmer",
                        "area_m2": 24.5,
                        "perimeter_m": 20.0,
                        "height_m": 0,  # Vision-uncertain placeholder
                        "ceiling_height_source": "grundriss",
                    },
                ],
            }
        ],
    }

    await _store_extraction_result(result, plan, db_session)
    await db_session.commit()

    room = (await db_session.execute(select(Room))).scalars().first()
    assert room is not None
    assert room.ceiling_height_source == "default"
    # height_m is 2.50 after the writeback — NOT 0.
    assert room.height_m is not None and abs(float(room.height_m) - 2.5) < 1e-6
