"""Tests for Malerarbeiten calculation engine (ÖNORM B 2230-1).

These tests verify the deterministic, traceable calculation of
quantities for painter work according to Austrian standards.
"""

import uuid
from decimal import Decimal

import pytest

from app.calculation_engine.trades.malerarbeiten import MalerarbeitenCalculator
from app.calculation_engine.types import RoomWithOpenings, OpeningData


@pytest.fixture
def calculator():
    return MalerarbeitenCalculator()


def _make_room(
    name: str = "Wohnzimmer",
    area: float = 20.0,
    perimeter: float = 18.0,
    height: float = 2.7,
    openings: list[OpeningData] | None = None,
    is_wet_room: bool = False,
    is_staircase: bool = False,
    has_dachschraege: bool = False,
) -> RoomWithOpenings:
    return RoomWithOpenings(
        id=uuid.uuid4(),
        name=name,
        room_type="wohnzimmer",
        area_m2=Decimal(str(area)),
        perimeter_m=Decimal(str(perimeter)),
        height_m=Decimal(str(height)),
        floor_type="parkett",
        wall_type="putz",
        ceiling_type=None,
        is_wet_room=is_wet_room,
        has_dachschraege=has_dachschraege,
        is_staircase=is_staircase,
        unit_name="Top 1",
        openings=openings or [],
    )


class TestMalerarbeitenBasic:
    """Basic wall and ceiling calculations."""

    def test_simple_room_wall_area(self, calculator):
        """Wall area = perimeter × height."""
        room = _make_room(perimeter=18.0, height=2.7)
        results = calculator.calculate([room])

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        # 18.0 × 2.7 = 48.6
        assert wall_pos.total_quantity == Decimal("48.600")

    def test_simple_room_ceiling_area(self, calculator):
        """Ceiling area = floor area."""
        room = _make_room(area=20.0)
        results = calculator.calculate([room])

        ceiling_pos = next(p for p in results if "Decken" in p.short_text)
        assert ceiling_pos.total_quantity == Decimal("20.000")

    def test_multiple_rooms_sum(self, calculator):
        """Quantities from multiple rooms are summed."""
        rooms = [
            _make_room(name="Zimmer 1", area=15.0, perimeter=16.0, height=2.5),
            _make_room(name="Zimmer 2", area=20.0, perimeter=18.0, height=2.5),
        ]
        results = calculator.calculate(rooms)

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        # (16.0 × 2.5) + (18.0 × 2.5) = 40.0 + 45.0 = 85.0
        assert wall_pos.total_quantity == Decimal("85.000")

        ceiling_pos = next(p for p in results if "Decken" in p.short_text)
        assert ceiling_pos.total_quantity == Decimal("35.000")


class TestMalerarbeitenDeductions:
    """Opening deductions per ÖNORM B 2230-1."""

    def test_small_opening_not_deducted(self, calculator):
        """Openings < 5.0 m² on plaster are NOT deducted."""
        room = _make_room(
            perimeter=18.0, height=2.7,
            openings=[OpeningData(opening_type="fenster", width_m=Decimal("1.2"), height_m=Decimal("1.5"))]
        )
        results = calculator.calculate([room])

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        # 18.0 × 2.7 = 48.6 — no deduction (1.2 × 1.5 = 1.8 < 5.0)
        assert wall_pos.total_quantity == Decimal("48.600")

        # Check deduction detail says "not deducted"
        line = wall_pos.measurement_lines[0]
        assert len(line.deductions) == 1
        assert line.deductions[0].deducted is False

    def test_large_opening_deducted(self, calculator):
        """Openings >= 5.0 m² on plaster ARE deducted."""
        room = _make_room(
            perimeter=18.0, height=2.7,
            openings=[OpeningData(opening_type="fenster", width_m=Decimal("2.5"), height_m=Decimal("2.2"))]
        )
        results = calculator.calculate([room])

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        # 18.0 × 2.7 = 48.6, deduction: 2.5 × 2.2 = 5.5 → net = 43.1
        assert wall_pos.total_quantity == Decimal("43.100")


class TestMalerarbeitenFactors:
    """ÖNORM surcharge factors."""

    def test_staircase_factor(self, calculator):
        """Staircase gets factor 1.5."""
        room = _make_room(
            name="Stiegenhaus", perimeter=12.0, height=2.7, area=10.0,
            is_staircase=True,
        )
        results = calculator.calculate([room])

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        # 12.0 × 2.7 × 1.5 = 48.6
        assert wall_pos.total_quantity == Decimal("48.600")

    def test_height_factor_over_320(self, calculator):
        """Height > 3.20m gets factor 1.12."""
        room = _make_room(perimeter=18.0, height=4.0)
        results = calculator.calculate([room])

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        # 18.0 × 4.0 × 1.12 = 80.64
        assert wall_pos.total_quantity == Decimal("80.640")

    def test_height_factor_over_500(self, calculator):
        """Height > 5.00m gets factor 1.16."""
        room = _make_room(perimeter=18.0, height=6.0)
        results = calculator.calculate([room])

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        # 18.0 × 6.0 × 1.16 = 125.28
        assert wall_pos.total_quantity == Decimal("125.280")


class TestMalerarbeitenLeibung:
    """Reveal (Leibung) calculations."""

    def test_leibung_calculated(self, calculator):
        """Reveals are calculated for all openings."""
        room = _make_room(
            openings=[
                OpeningData(opening_type="fenster", width_m=Decimal("1.2"), height_m=Decimal("1.5")),
                OpeningData(opening_type="tuer", width_m=Decimal("0.9"), height_m=Decimal("2.1")),
            ]
        )
        results = calculator.calculate([room])

        leibung_pos = next((p for p in results if "Leibung" in p.short_text), None)
        assert leibung_pos is not None
        # Fenster: (1.2 + 1.5) × 2 × 0.20 = 1.08
        # Tuer: (0.9 + 2.1) × 2 × 0.20 = 1.20
        # Total: 2.28
        assert leibung_pos.total_quantity == Decimal("2.280")


class TestMalerarbeitenTraceability:
    """Every calculated quantity must be traceable."""

    def test_measurement_lines_have_room_reference(self, calculator):
        room = _make_room()
        results = calculator.calculate([room])

        for pos in results:
            for line in pos.measurement_lines:
                assert line.room_id
                assert line.room_name == "Wohnzimmer"

    def test_measurement_lines_have_onorm_reference(self, calculator):
        room = _make_room()
        results = calculator.calculate([room])

        for pos in results:
            for line in pos.measurement_lines:
                assert line.onorm_rule_ref.startswith("B2230-1")
                assert "ÖNORM B 2230-1" in line.onorm_paragraph

    def test_measurement_lines_have_formula(self, calculator):
        room = _make_room(perimeter=18.0, height=2.7)
        results = calculator.calculate([room])

        wall_pos = next(p for p in results if "Wand" in p.short_text)
        line = wall_pos.measurement_lines[0]
        assert "18" in line.formula_expression
        assert "2.7" in line.formula_expression
        assert "Umfang" in line.formula_description
