"""Tests for the v24.2 Schnitt-Plan height-extraction pipeline.

We deliberately exercise the two pure helpers that do all of the
real work — ``_sanitize_schnitt_heights`` and
``_apply_schnitt_heights_to_rooms`` — rather than the full
``analyze_schnitt_plan`` end-to-end. The full function:

  1. Hits Anthropic Vision (network).
  2. Renders a PDF via PyMuPDF.
  3. Mutates ``Plan.analysis_status``.

Mocking all three for a happy-path is high-effort and brittle;
testing the two helpers gives us the real coverage we care about
(does the matching logic write the right ``height_m`` onto the
right ``Room``? does it skip ambiguous cases? does it cope when
nothing matches?) without faking the world around it.

Three cases:

  1. **No match** — Vision returned heights for rooms that don't
     exist in the project. Nothing is written. Returns 0.
  2. **Full match** — every extracted entry hits exactly one room.
     Each room gets ``height_m`` set and ``ceiling_height_source``
     flips to ``"schnitt"``. Returns N.
  3. **Partial match** — mix of: name+floor match, name-only match,
     name-only-but-ambiguous (must skip), and no-name-match. Only
     the unambiguous ones are written; the ambiguous one keeps its
     previous height.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.models.user import User
from app.plan_analysis.pipeline import (
    _apply_schnitt_heights_to_rooms,
    _sanitize_schnitt_heights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_project_with_rooms(
    db: AsyncSession,
    *,
    rooms_spec: list[tuple[str, str, Decimal | None]],
) -> uuid.UUID:
    """Seed a User → Project → Building → Floor[N] → Unit → Room chain.

    ``rooms_spec`` is a list of ``(room_name, floor_name, initial_height)``
    triples. Floors with the same name are merged so multiple rooms
    can share a floor. ``initial_height`` is the pre-Schnitt-run
    height (``None`` means the room hasn't been heightened yet).

    Returns the project_id so the test can hand it to
    ``_apply_schnitt_heights_to_rooms``.
    """
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

    building = Building(id=uuid.uuid4(), project_id=project.id, name="Haus 1")
    db.add(building)
    await db.flush()

    # Floor cache by name so multiple rooms on the same floor merge.
    floors: dict[str, Floor] = {}
    for _, floor_name, _ in rooms_spec:
        if floor_name in floors:
            continue
        floor = Floor(id=uuid.uuid4(), building_id=building.id, name=floor_name)
        db.add(floor)
        floors[floor_name] = floor
    await db.flush()

    # One unit per floor — Schnitt matching doesn't care about units
    # (it joins through to floor), so a single unit per floor is fine.
    units: dict[str, Unit] = {}
    for floor_name, floor in floors.items():
        unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name=f"Top {floor_name}")
        db.add(unit)
        units[floor_name] = unit
    await db.flush()

    for room_name, floor_name, initial_height in rooms_spec:
        room = Room(
            id=uuid.uuid4(),
            unit_id=units[floor_name].id,
            name=room_name,
            height_m=initial_height,
        )
        db.add(room)
    await db.commit()
    return project.id


def _floats_close(a: object, b: float, *, tol: float = 1e-6) -> bool:
    if a is None:
        return False
    return abs(float(a) - b) < tol


# ---------------------------------------------------------------------------
# _sanitize_schnitt_heights — defensive coverage
# ---------------------------------------------------------------------------


def test_sanitize_drops_missing_name_and_height_and_out_of_range():
    """The sanitiser is the only line of defence between
    Vision-hallucinated noise and the matcher. Three failure modes
    have to go: no raumname, non-numeric height, plausibility bust."""
    raw = [
        {"raumname": "Wohnen", "hoehe_m": 2.5},   # keep
        {"raumname": "", "hoehe_m": 2.5},          # drop: no name
        {"raumname": "Bad", "hoehe_m": "x"},       # drop: not a number
        {"raumname": "Keller", "hoehe_m": 0.4},    # drop: below MIN
        {"raumname": "Dachfirst", "hoehe_m": 9.0}, # drop: above MAX
        {"raumname": "Schlafen", "höhe_m": 2.4},   # keep: umlaut alias
    ]

    out = _sanitize_schnitt_heights(raw)

    names = {e["raumname"] for e in out}
    assert names == {"Wohnen", "Schlafen"}
    # ``hoehe_m`` is the canonical key on output regardless of input alias.
    for entry in out:
        assert isinstance(entry["hoehe_m"], float)


# ---------------------------------------------------------------------------
# Case 1 — no match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_no_match_writes_nothing(db_session: AsyncSession):
    """Vision found heights for rooms that don't exist in the project.
    The matcher must return 0 and leave every ``height_m`` untouched."""
    project_id = await _seed_project_with_rooms(
        db_session,
        rooms_spec=[
            ("Wohnen", "EG", Decimal("2.7")),
            ("Bad", "EG", None),
        ],
    )

    extracted = [
        # Both names don't exist in the project — pure miss.
        {"raumname": "Konferenzraum", "geschoss": "EG", "hoehe_m": 3.0},
        {"raumname": "Tiefgarage", "geschoss": "KG", "hoehe_m": 2.5},
    ]

    matched = await _apply_schnitt_heights_to_rooms(
        extracted, project_id, db_session
    )

    assert matched == 0

    # Sanity: project's rooms still carry their pre-run state.
    from sqlalchemy import select

    rooms = (
        await db_session.execute(select(Room).order_by(Room.name))
    ).scalars().all()
    by_name = {r.name: r for r in rooms}
    assert _floats_close(by_name["Wohnen"].height_m, 2.7)
    assert by_name["Bad"].height_m is None
    # ``ceiling_height_source`` stays on the default for both — we
    # never touched it.
    assert by_name["Wohnen"].ceiling_height_source == "default"
    assert by_name["Bad"].ceiling_height_source == "default"


# ---------------------------------------------------------------------------
# Case 2 — full match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_full_match_writes_every_height(db_session: AsyncSession):
    """Every extracted entry maps onto exactly one room via the
    name+floor tier. All heights land, all source-tags flip."""
    project_id = await _seed_project_with_rooms(
        db_session,
        rooms_spec=[
            ("Wohnen", "EG", None),
            ("Bad", "EG", None),
            ("Schlafen", "1.OG", None),
        ],
    )

    extracted = [
        # Names + floor labels intentionally include the typographic
        # quirks the normaliser folds away ("1.OG" vs "1og", lowercase
        # vs uppercase) so we exercise the normalisation path too.
        {"raumname": "WOHNEN", "geschoss": "EG", "hoehe_m": 2.5},
        {"raumname": "bad", "geschoss": "eg", "hoehe_m": 2.4},
        {"raumname": "Schlafen", "geschoss": "1.OG", "hoehe_m": 2.45},
    ]

    matched = await _apply_schnitt_heights_to_rooms(
        extracted, project_id, db_session
    )
    await db_session.commit()

    assert matched == 3

    from sqlalchemy import select

    rooms = (
        await db_session.execute(select(Room).order_by(Room.name))
    ).scalars().all()
    by_name = {r.name: r for r in rooms}
    assert _floats_close(by_name["Wohnen"].height_m, 2.5)
    assert _floats_close(by_name["Bad"].height_m, 2.4)
    assert _floats_close(by_name["Schlafen"].height_m, 2.45)
    # Source-tag flipped to "schnitt" on every touched room.
    for r in rooms:
        assert r.ceiling_height_source == "schnitt", (
            f"room {r.name!r} kept source {r.ceiling_height_source!r}"
        )


# ---------------------------------------------------------------------------
# Case 3 — partial match (the realistic case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_partial_match_skips_ambiguous_and_unknown(
    db_session: AsyncSession,
):
    """Realistic mix:

      * "Wohnen" / EG     — unambiguous, matches via tier 1 (name+floor)
      * "Schlafen"         — Vision omitted floor; only ONE Schlafen
                             in the project, so tier 2 (name-only) hits
      * "Bad"              — TWO Bad rooms (EG + 1.OG) and Vision
                             didn't say which floor → ambiguous, skip
      * "Garage"           — name doesn't exist in the project at all
                             → no match, skip

    Expected: 2 of 4 matched. Wohnen + Schlafen carry their new
    heights with source="schnitt"; both Bad rooms keep their
    pre-existing values untouched."""
    project_id = await _seed_project_with_rooms(
        db_session,
        rooms_spec=[
            ("Wohnen", "EG", None),
            ("Bad", "EG", Decimal("2.7")),
            ("Bad", "1.OG", Decimal("2.6")),
            ("Schlafen", "1.OG", None),
        ],
    )

    extracted = [
        {"raumname": "Wohnen", "geschoss": "EG", "hoehe_m": 2.5},
        {"raumname": "Schlafen", "hoehe_m": 2.4},  # no floor → tier 2
        {"raumname": "Bad", "hoehe_m": 2.45},      # ambiguous, skip
        {"raumname": "Garage", "geschoss": "KG", "hoehe_m": 2.3},  # unknown
    ]

    matched = await _apply_schnitt_heights_to_rooms(
        extracted, project_id, db_session
    )
    await db_session.commit()

    assert matched == 2

    from sqlalchemy import select

    rooms = (
        await db_session.execute(select(Room))
    ).scalars().all()
    by_name_floor: dict[tuple[str, str], Room] = {}
    for r in rooms:
        # Cheaply walk back to the floor name — needed because we
        # have two ``Bad`` rooms on different floors.
        floor = await db_session.get(Floor, (
            await db_session.get(Unit, r.unit_id)
        ).floor_id)
        by_name_floor[(r.name, floor.name)] = r

    # Wohnen got its tier-1 match.
    wohnen = by_name_floor[("Wohnen", "EG")]
    assert _floats_close(wohnen.height_m, 2.5)
    assert wohnen.ceiling_height_source == "schnitt"

    # Schlafen got its tier-2 (name-only) match.
    schlafen = by_name_floor[("Schlafen", "1.OG")]
    assert _floats_close(schlafen.height_m, 2.4)
    assert schlafen.ceiling_height_source == "schnitt"

    # Both Bad rooms are untouched — ambiguous match was skipped.
    bad_eg = by_name_floor[("Bad", "EG")]
    bad_og = by_name_floor[("Bad", "1.OG")]
    assert _floats_close(bad_eg.height_m, 2.7)
    assert _floats_close(bad_og.height_m, 2.6)
    assert bad_eg.ceiling_height_source == "default"
    assert bad_og.ceiling_height_source == "default"
