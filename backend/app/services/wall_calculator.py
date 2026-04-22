"""Wandflächen- und Umfangberechnung für Räume.

Pure-Python helpers that turn the geometry we already extract from
plans (perimeter + ceiling height + openings) into the wall-surface
numbers an Austrian estimator actually works with.

The rules encoded here follow common Austrian building practice. We
deliberately avoid naming specific norms in user-facing text (the
frontend calls this "Berechnung nach österreichischen Baustandards");
the rationale lives in code comments, not on screen.

Conventions baked in:

* **Stairwells** (``is_staircase=True``) carry a 1.5× surcharge for
  multi-storey wall development. A single-height staircase is still a
  normal room in this model — set ``is_staircase`` only when the
  stairwell has multi-storey wall development.
* **Ceiling-height surcharges** (non-stairwell rooms):
    - ``height_m > 4.0``  → factor 1.16
    - ``height_m  ≥ 3.0``  → factor 1.12
    - otherwise            → factor 1.00
  The surcharge accounts for scaffolding/rigging overhead for tall
  walls — it's applied at the gross level before opening deduction.
* **Openings**: window/door openings with individual area ≥ 2.5 m²
  are subtracted from gross. Below-threshold openings are kept in
  the net area (common Austrian practice: small openings aren't
  priced out because cut-around effort eats the material savings).
  The threshold is inclusive — 2.5 m² exactly *is* deducted.
* **Deductions toggle**: when a room's ``deductions_enabled`` flag
  is false, gross == net regardless of opening sizes. This is the
  conservative override an estimator can flip when they want to
  price the wall as if it had no openings.

All numeric results are rounded to 2 decimals — the precision the
frontend displays and the LV exporter consumes.

This module is intentionally free of database and async code. The
service takes plain dataclasses so unit tests don't need an async
session and the calculation can be exercised deterministically from
a REPL.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


# Surcharge multipliers. Kept as module-level constants so tests can
# assert against the exact values and future rule changes are a
# single-line edit reviewed in isolation.
STAIRCASE_FACTOR: float = 1.5
HEIGHT_FACTOR_OVER_4M: float = 1.16
HEIGHT_FACTOR_3M_TO_4M: float = 1.12
DEFAULT_FACTOR: float = 1.0

# Threshold above which an opening is subtracted from the net wall
# area. Inclusive — a 2.5 m² opening *is* deducted.
OPENING_DEDUCTION_THRESHOLD_M2: float = 2.5

# Fallback ceiling height used when nothing else is known. 2.50 m is
# the conservative Austrian residential default; rooms that hit this
# path are marked ``ceiling_height_source='default'`` so the UI can
# highlight them in amber and prompt the user to confirm.
DEFAULT_CEILING_HEIGHT_M: float = 2.5


@dataclass(frozen=True)
class OpeningInput:
    """Single opening (window/door) as the calculator sees it.

    ``count`` mirrors ``Opening.count`` — a row like "3 identical
    windows in one wall" is stored as one Opening with ``count=3``,
    and the calculator treats each individual opening independently
    for threshold comparison: three 1 m² windows stay undeducted
    because each one is below 2.5 m², not because 3 m² total is
    below the threshold.
    """

    width_m: float
    height_m: float
    count: int = 1

    @property
    def single_area_m2(self) -> float:
        return float(self.width_m) * float(self.height_m)

    @property
    def total_area_m2(self) -> float:
        return self.single_area_m2 * max(self.count, 1)


@dataclass(frozen=True)
class WallCalculationResult:
    """Everything the service returns for one room.

    Kept flat and JSON-serialisable so the API layer can hand it to
    Pydantic or dump it to the frontend without reshaping.
    """

    wall_area_gross_m2: float
    wall_area_net_m2: float
    applied_factor: float
    deductions_total_m2: float
    deductions_considered_count: int
    perimeter_m: float
    height_used_m: float
    ceiling_height_source: str


def _resolve_factor(height_m: float, is_staircase: bool) -> float:
    """Pick the multiplier for a room.

    Staircase wins over the height ladder — a 5 m stairwell gets 1.5,
    not 1.16, because the stairwell surcharge already covers the
    multi-storey rigging the tall-room surcharge would have applied
    for. We don't compound the two.
    """

    if is_staircase:
        return STAIRCASE_FACTOR
    if height_m > 4.0:
        return HEIGHT_FACTOR_OVER_4M
    if height_m >= 3.0:
        return HEIGHT_FACTOR_3M_TO_4M
    return DEFAULT_FACTOR


def _round2(value: float) -> float:
    """Round half-up to 2 decimals (the precision the UI and LV use).

    ``round()`` in Python uses banker's rounding which can surprise
    users ("2.005 → 2.00"); Decimal.quantize with ROUND_HALF_UP is
    closer to what the frontend's ``toFixed(2)`` produces and what
    the estimator expects on paper.
    """

    from decimal import ROUND_HALF_UP

    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_wall_areas(
    *,
    perimeter_m: float | None,
    height_m: float | None,
    is_staircase: bool,
    deductions_enabled: bool,
    openings: list[OpeningInput],
    ceiling_height_source: str = "default",
) -> WallCalculationResult:
    """Compute gross / net wall area for a single room.

    Parameters mirror the Room columns the API layer will extract
    from SQLAlchemy. ``perimeter_m`` or ``height_m`` being ``None``
    is a soft failure: we substitute 0 / the default height and still
    return a result so the frontend can show "something" — the caller
    decides whether to display a warning based on ``ceiling_height_source``
    and the perimeter value.
    """

    peri = float(perimeter_m) if perimeter_m is not None else 0.0
    if height_m is None or float(height_m) <= 0.0:
        height_used = DEFAULT_CEILING_HEIGHT_M
        source = "default"
    else:
        height_used = float(height_m)
        source = ceiling_height_source

    factor = _resolve_factor(height_used, is_staircase)
    gross = peri * height_used * factor

    deductions_total = 0.0
    deductions_count = 0
    if deductions_enabled:
        for op in openings:
            single = op.single_area_m2
            if single >= OPENING_DEDUCTION_THRESHOLD_M2:
                # Deduct each instance — a row with count=3 of 3 m²
                # openings subtracts 9 m², not 3 m².
                deductions_total += op.total_area_m2
                deductions_count += max(op.count, 1)

    net = max(gross - deductions_total, 0.0)

    return WallCalculationResult(
        wall_area_gross_m2=_round2(gross),
        wall_area_net_m2=_round2(net),
        applied_factor=factor,
        deductions_total_m2=_round2(deductions_total),
        deductions_considered_count=deductions_count,
        perimeter_m=_round2(peri),
        height_used_m=_round2(height_used),
        ceiling_height_source=source,
    )


def openings_from_orm(room_openings) -> list[OpeningInput]:
    """Convert SQLAlchemy ``Opening`` rows into calculator inputs.

    Exists so API/service code that already has ORM objects in hand
    doesn't need to reach into ``.width_m`` / ``.height_m`` / ``.count``
    by hand on every call.
    """

    return [
        OpeningInput(
            width_m=float(o.width_m or 0),
            height_m=float(o.height_m or 0),
            count=int(o.count or 1),
        )
        for o in room_openings
    ]
