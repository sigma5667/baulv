"""Tests for the Wandflächenberechnung service.

The calculation rules live in
``app/services/wall_calculator.py``; the rationale for each rule is
in that module's docstring. These tests pin the numbers so any
future refactor has to notice when it changes behaviour.
"""

from app.services.wall_calculator import (
    DEFAULT_CEILING_HEIGHT_M,
    OpeningInput,
    calculate_wall_areas,
)


def test_normal_room_no_openings_no_surcharge():
    """10 m perimeter × 2.7 m height = 27 m², factor 1.0, no deductions."""

    result = calculate_wall_areas(
        perimeter_m=10.0,
        height_m=2.7,
        is_staircase=False,
        deductions_enabled=True,
        openings=[],
        ceiling_height_source="manual",
    )
    assert result.applied_factor == 1.0
    assert result.wall_area_gross_m2 == 27.0
    assert result.wall_area_net_m2 == 27.0
    assert result.deductions_considered_count == 0
    assert result.ceiling_height_source == "manual"


def test_staircase_applies_1_5_factor():
    """Stairwell multiplies by 1.5 regardless of height."""

    result = calculate_wall_areas(
        perimeter_m=12.0,
        height_m=2.8,
        is_staircase=True,
        deductions_enabled=True,
        openings=[],
    )
    # 12 × 2.8 × 1.5 = 50.4
    assert result.applied_factor == 1.5
    assert result.wall_area_gross_m2 == 50.4
    assert result.wall_area_net_m2 == 50.4


def test_tall_room_3m_to_4m_gets_surcharge_1_12():
    """Ceiling ≥ 3.0 m (and ≤ 4.0 m) → factor 1.12."""

    result = calculate_wall_areas(
        perimeter_m=10.0,
        height_m=3.5,
        is_staircase=False,
        deductions_enabled=True,
        openings=[],
    )
    # 10 × 3.5 × 1.12 = 39.2
    assert result.applied_factor == 1.12
    assert result.wall_area_gross_m2 == 39.2


def test_very_tall_room_over_4m_gets_surcharge_1_16():
    """Ceiling > 4.0 m → factor 1.16 (beats 3–4 m threshold)."""

    result = calculate_wall_areas(
        perimeter_m=10.0,
        height_m=4.5,
        is_staircase=False,
        deductions_enabled=True,
        openings=[],
    )
    # 10 × 4.5 × 1.16 = 52.2
    assert result.applied_factor == 1.16
    assert result.wall_area_gross_m2 == 52.2


def test_small_openings_under_threshold_are_not_deducted():
    """Openings < 2.5 m² stay in the wall (Austrian convention)."""

    result = calculate_wall_areas(
        perimeter_m=10.0,
        height_m=2.7,
        is_staircase=False,
        deductions_enabled=True,
        openings=[
            # 1.0 × 1.2 = 1.2 m² (below 2.5 m²) × 2 → not deducted.
            OpeningInput(width_m=1.0, height_m=1.2, count=2),
            # 1.0 × 2.1 = 2.1 m² (still below 2.5 m²) → not deducted.
            OpeningInput(width_m=1.0, height_m=2.1, count=1),
        ],
    )
    assert result.wall_area_gross_m2 == 27.0
    assert result.wall_area_net_m2 == 27.0
    assert result.deductions_total_m2 == 0.0
    assert result.deductions_considered_count == 0


def test_large_opening_at_or_above_threshold_is_deducted():
    """Openings ≥ 2.5 m² are subtracted; threshold is inclusive.

    Also exercises: (a) a count=2 opening contributes both instances
    to the deduction, (b) the threshold applies per instance, not to
    the combined area.
    """

    result = calculate_wall_areas(
        perimeter_m=12.0,
        height_m=2.7,
        is_staircase=False,
        deductions_enabled=True,
        openings=[
            # Exactly 2.5 m² → threshold hit → deducted.
            OpeningInput(width_m=1.25, height_m=2.0, count=1),
            # 1.2 × 2.2 = 2.64 m² × 2 instances = 5.28 m² deducted.
            OpeningInput(width_m=1.2, height_m=2.2, count=2),
            # 0.6 × 2.0 = 1.2 m² each — below threshold, not deducted.
            OpeningInput(width_m=0.6, height_m=2.0, count=3),
        ],
    )
    # Gross: 12 × 2.7 × 1.0 = 32.4
    # Deducted: 2.5 + 2 × 2.64 = 7.78
    assert result.wall_area_gross_m2 == 32.4
    assert result.wall_area_net_m2 == 24.62
    assert result.deductions_total_m2 == 7.78
    assert result.deductions_considered_count == 3  # 1 + 2


def test_deductions_disabled_keeps_gross_equal_to_net():
    """Toggle off → the big opening does not reduce net."""

    result = calculate_wall_areas(
        perimeter_m=12.0,
        height_m=2.7,
        is_staircase=False,
        deductions_enabled=False,
        openings=[
            OpeningInput(width_m=2.0, height_m=2.1, count=1),  # 4.2 m²
        ],
    )
    assert result.wall_area_gross_m2 == 32.4
    assert result.wall_area_net_m2 == 32.4
    assert result.deductions_total_m2 == 0.0


def test_missing_height_falls_back_to_default():
    """height_m=None → use 2.50 m and mark source as ``default``."""

    result = calculate_wall_areas(
        perimeter_m=10.0,
        height_m=None,
        is_staircase=False,
        deductions_enabled=True,
        openings=[],
        ceiling_height_source="schnitt",
    )
    assert result.height_used_m == DEFAULT_CEILING_HEIGHT_M
    assert result.ceiling_height_source == "default"
    assert result.wall_area_gross_m2 == 25.0
