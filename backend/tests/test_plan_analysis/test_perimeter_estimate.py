"""Tests for ``_resolve_perimeter`` — the room-perimeter triage helper.

The helper is the boundary between Vision-extracted JSON and the DB
write. Three branches we want to lock down:

1. Vision returned a positive perimeter → trust it, tag ``vision``.
2. Vision returned no perimeter but a positive area → fall back to
   the ``4 · √A · 1.10`` estimate, tag ``estimated``.
3. Neither → both ``None``, frontend renders the empty-state badge.

The same formula is encoded in PG-SQL inside migration 016. If this
test ever needs to change, the migration's UPDATE clause needs to
change in lock-step.
"""

from __future__ import annotations

import math

import pytest

from app.plan_analysis.pipeline import _resolve_perimeter


# ---------------------------------------------------------------------------
# Branch 1: Vision returned a positive perimeter
# ---------------------------------------------------------------------------


def test_vision_value_passes_through_unchanged():
    """Vision-extracted perimeter wins over any area — we trust it."""
    perimeter, source = _resolve_perimeter(18.5, 25.0)
    assert perimeter == 18.5
    assert source == "vision"


def test_vision_value_passes_through_without_area():
    """No area + Vision-extracted perimeter still works — most plans
    Vision can read a perimeter off don't necessarily yield an area."""
    perimeter, source = _resolve_perimeter(12.3, None)
    assert perimeter == 12.3
    assert source == "vision"


# ---------------------------------------------------------------------------
# Branch 2: Vision returned no perimeter but an area exists
# ---------------------------------------------------------------------------


def test_estimate_from_area_uses_canonical_formula():
    """20 m² → 4 · √20 · 1.10 = 19.6774 → rounded 19.68 m.

    The user spec illustrates the formula as ``4 × 4.47 × 1.1 = 19.67``,
    pre-rounding √20 to 4.47 for mental math. Full-precision √20 is
    4.4721…, which moves the third decimal to 7 — and Python's
    ``round`` (and PG's) lands on 19.68. The 1 cm delta is well
    inside the m² uncertainty Vision extracts areas with anyway.
    """
    perimeter, source = _resolve_perimeter(None, 20.0)
    expected = round(4 * math.sqrt(20.0) * 1.10, 2)
    assert perimeter == expected
    assert perimeter == 19.68
    assert source == "estimated"


def test_estimate_for_small_room():
    """A 4 m² room — 4 · 2 · 1.10 = 8.80 m exactly."""
    perimeter, source = _resolve_perimeter(None, 4.0)
    assert perimeter == 8.80
    assert source == "estimated"


def test_estimate_for_large_room():
    """A 100 m² room — 4 · 10 · 1.10 = 44.00 m exactly. Sanity check
    that we don't fall over on bigger numbers."""
    perimeter, source = _resolve_perimeter(None, 100.0)
    assert perimeter == 44.00
    assert source == "estimated"


# ---------------------------------------------------------------------------
# Branch 3: Genuinely unknown — no perimeter, no area
# ---------------------------------------------------------------------------


def test_no_data_returns_none_pair():
    """No Vision input at all — the row stays unflagged so the
    frontend can render the red 'Bitte eintragen' fallback."""
    perimeter, source = _resolve_perimeter(None, None)
    assert perimeter is None
    assert source is None


def test_zero_perimeter_treated_as_missing():
    """Vision shouldn't return 0, but if it does we don't want to
    persist a 0 m perimeter — that produces gross=0 forever. Treat
    it as missing and let the area-based fallback kick in."""
    perimeter, source = _resolve_perimeter(0, 16.0)
    expected = round(4 * math.sqrt(16.0) * 1.10, 2)
    assert perimeter == expected
    assert source == "estimated"


def test_zero_area_treated_as_missing():
    """Mirror of the above for area — 0 means absent, not the
    smallest possible room."""
    perimeter, source = _resolve_perimeter(None, 0)
    assert perimeter is None
    assert source is None


def test_negative_area_treated_as_missing():
    """Defensive: a hallucinated negative area shouldn't crash
    sqrt() — we treat it as absent."""
    perimeter, source = _resolve_perimeter(None, -5.0)
    assert perimeter is None
    assert source is None


# ---------------------------------------------------------------------------
# Type robustness — the JSON payload from Vision is loosely typed
# ---------------------------------------------------------------------------


def test_int_perimeter_input_is_floatified():
    """Vision sometimes returns an int instead of a float; we
    promote it without losing precision."""
    perimeter, source = _resolve_perimeter(18, None)
    assert perimeter == 18.0
    assert isinstance(perimeter, float)
    assert source == "vision"


def test_int_area_input_is_floatified():
    perimeter, source = _resolve_perimeter(None, 25)
    expected = round(4 * math.sqrt(25.0) * 1.10, 2)
    assert perimeter == expected
    assert source == "estimated"


# ---------------------------------------------------------------------------
# Migration parity — the PG-SQL formula in 016 must match this
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "area_m2, expected",
    [
        (4.0, 8.80),  # 4 * 2 * 1.10  (exact)
        (9.0, 13.20),  # 4 * 3 * 1.10  (exact)
        (20.0, 19.68),  # full-precision √20 — see test above
        (50.0, 31.11),  # 4 * 7.0710… * 1.10
        (100.0, 44.00),  # 4 * 10 * 1.10  (exact)
    ],
)
def test_estimate_table_matches_migration_sql(area_m2: float, expected: float):
    """Lock the formula to specific area→perimeter pairs. If anyone
    ever tunes the 1.10 fudge factor, this table must be updated AND
    the migration's UPDATE clause in
    ``alembic/versions/016_perimeter_source_backfill.py`` must be
    updated in the same change. The parity is the whole reason the
    formula lives in two places."""
    perimeter, source = _resolve_perimeter(None, area_m2)
    assert perimeter == expected
    assert source == "estimated"
