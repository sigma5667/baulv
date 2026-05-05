"""Maler- und Beschichtungsarbeiten — Mengenermittlung.

Deterministic calculation of all quantities for painter work
according to standard Austrian construction-industry measurement
rules. The rule references in this module are deliberately neutral —
``rule_ref`` strings carry internal slugs (e.g. ``wandbeschichtung``)
and ``rule_paragraph`` strings carry plain-language section labels.

Key rules implemented:
- Wall painting: perimeter × height, deductions for openings > 5.0 m² (on plaster/concrete)
- Wall+ceiling combined: factor 1.2 when ceiling strip ≤ 1m
- Ceiling painting: floor area
- Staircase surcharge: factor 1.5
- Height surcharge >3.20m: factor 1.12; >5.00m: factor 1.16
- Wet room preparation surcharge
- Leibung (reveals): opening perimeter × depth
- Multiple colors: factor 1.1 per additional color
"""

from decimal import Decimal

from app.calculation_engine.base_trade import TradeCalculator
from app.calculation_engine.registry import TradeRegistry
from app.calculation_engine.types import (
    RoomWithOpenings,
    MeasurementLine,
    PositionQuantity,
)


@TradeRegistry.register
class MalerarbeitenCalculator(TradeCalculator):

    trade_name = "malerarbeiten"
    # Internal label kept for backwards-compat with the abstract base
    # class. Surfaced nowhere user-visible. Public API only renders
    # the per-line ``rule_ref`` / ``rule_paragraph`` values.
    onorm_reference = "Maler-Beschichtung"

    # Calculation constants for wall and ceiling painting.
    DEDUCTION_THRESHOLD_PUTZ_M2 = Decimal("5.0")     # Openings on plaster/concrete: deduct >5.0 m²
    DEDUCTION_THRESHOLD_HOLZ_M2 = Decimal("0.5")     # Openings on wood/metal: deduct >0.5 m²
    STAIRCASE_FACTOR = Decimal("1.5")                  # Treppenhaus factor
    WALL_CEILING_COMBINED_FACTOR = Decimal("1.2")      # Wand+Deckenstreifen ≤1m
    HEIGHT_FACTOR_320_500 = Decimal("1.12")            # Höhenzuschlag >3.20m bis 5.00m
    HEIGHT_FACTOR_OVER_500 = Decimal("1.16")           # Höhenzuschlag >5.00m
    DEFAULT_REVEAL_DEPTH_M = Decimal("0.20")           # Standard Leibungstiefe
    MULTI_COLOR_FACTOR = Decimal("1.1")                # Factor per additional color
    MIN_AREA_CHARGE = Decimal("0.25")                  # Minimum charge area

    def calculate(self, rooms: list[RoomWithOpenings]) -> list[PositionQuantity]:
        wall_lines: list[MeasurementLine] = []
        ceiling_lines: list[MeasurementLine] = []
        leibung_lines: list[MeasurementLine] = []

        for room in rooms:
            if not room.area_m2 or not room.perimeter_m or not room.height_m:
                continue

            # --- Wall area (Wandfläche) ---
            wall_line = self._calc_wall(room)
            if wall_line:
                wall_lines.append(wall_line)

            # --- Ceiling area (Deckenfläche) ---
            ceiling_line = self._calc_ceiling(room)
            if ceiling_line:
                ceiling_lines.append(ceiling_line)

            # --- Leibung (reveals) ---
            if room.openings:
                leibung_line = self._calc_leibung(room)
                if leibung_line:
                    leibung_lines.append(leibung_line)

        positions: list[PositionQuantity] = []

        if wall_lines:
            positions.append(PositionQuantity(
                position_code="01.01",
                short_text="Wandbeschichtung Dispersion weiß, 2× Anstrich auf vorbehandeltem Untergrund",
                unit="m2",
                total_quantity=self._round(sum(l.net_quantity for l in wall_lines)),
                measurement_lines=wall_lines,
                gruppe_name="Wandanstrich",
                gruppe_nummer="01",
            ))

        if ceiling_lines:
            positions.append(PositionQuantity(
                position_code="02.01",
                short_text="Deckenbeschichtung Dispersion weiß, 2× Anstrich auf vorbehandeltem Untergrund",
                unit="m2",
                total_quantity=self._round(sum(l.net_quantity for l in ceiling_lines)),
                measurement_lines=ceiling_lines,
                gruppe_name="Deckenanstrich",
                gruppe_nummer="02",
            ))

        if leibung_lines:
            positions.append(PositionQuantity(
                position_code="03.01",
                short_text="Leibungsbeschichtung Dispersion weiß, 2× Anstrich",
                unit="m2",
                total_quantity=self._round(sum(l.net_quantity for l in leibung_lines)),
                measurement_lines=leibung_lines,
                gruppe_name="Leibungsanstrich",
                gruppe_nummer="03",
            ))

        return positions

    def _calc_wall(self, room: RoomWithOpenings) -> MeasurementLine | None:
        gross_wall = self._wall_area(room)

        # Determine deduction threshold based on wall type
        threshold = self.DEDUCTION_THRESHOLD_PUTZ_M2
        if room.wall_type and room.wall_type.lower() in ("holz", "metall"):
            threshold = self.DEDUCTION_THRESHOLD_HOLZ_M2

        deduction_total, deduction_details = self._opening_deductions(room, threshold)

        # Determine factors
        factors: list[tuple[str, Decimal]] = []

        # Staircase factor
        if room.is_staircase:
            factors.append(("Treppenhaus §4.3", self.STAIRCASE_FACTOR))

        # Height surcharge
        height_factor = self._height_factor(room.height_m)
        if height_factor > Decimal("1.0"):
            factors.append((f"Höhenzuschlag RH={room.height_m}m", height_factor))

        combined_factor = Decimal("1.0")
        for _, f in factors:
            combined_factor *= f

        net = (gross_wall - deduction_total) * combined_factor
        net = max(net, Decimal("0"))

        factor_desc = ""
        if factors:
            factor_desc = " × ".join(f"{name} ({f})" for name, f in factors)

        formula_parts = [f"{room.perimeter_m} × {room.height_m}"]
        if deduction_total > 0:
            formula_parts.append(f"- {deduction_total}")
        if combined_factor != Decimal("1.0"):
            formula_parts.append(f"× {combined_factor}")

        return MeasurementLine(
            room_id=str(room.id),
            room_name=room.name,
            description=f"Wandfläche {room.name} ({room.unit_name})",
            formula_description=f"Umfang × Höhe"
                + (f" - Abzüge (>{threshold}m²)" if deduction_total > 0 else "")
                + (f" × {factor_desc}" if factor_desc else ""),
            formula_expression=" ".join(formula_parts),
            raw_quantity=self._round(gross_wall),
            onorm_factor=self._round(combined_factor, 4),
            onorm_rule_ref="wandbeschichtung",
            onorm_paragraph="§3.2 Wandbeschichtung",
            deductions=deduction_details,
            net_quantity=self._round(net),
            unit="m2",
        )

    def _calc_ceiling(self, room: RoomWithOpenings) -> MeasurementLine | None:
        ceiling = self._ceiling_area(room)

        factors: list[tuple[str, Decimal]] = []
        if room.is_staircase:
            factors.append(("Treppenhaus §4.3", self.STAIRCASE_FACTOR))

        combined_factor = Decimal("1.0")
        for _, f in factors:
            combined_factor *= f

        net = ceiling * combined_factor
        factor_desc = ""
        if factors:
            factor_desc = " × ".join(f"{name} ({f})" for name, f in factors)

        formula_parts = [f"{room.area_m2}"]
        if combined_factor != Decimal("1.0"):
            formula_parts.append(f"× {combined_factor}")

        return MeasurementLine(
            room_id=str(room.id),
            room_name=room.name,
            description=f"Deckenfläche {room.name} ({room.unit_name})",
            formula_description="Grundfläche = Deckenfläche"
                + (f" × {factor_desc}" if factor_desc else ""),
            formula_expression=" ".join(formula_parts),
            raw_quantity=self._round(ceiling),
            onorm_factor=self._round(combined_factor, 4),
            onorm_rule_ref="deckenbeschichtung",
            onorm_paragraph="§3.3 Deckenbeschichtung",
            deductions=[],
            net_quantity=self._round(net),
            unit="m2",
        )

    def _calc_leibung(self, room: RoomWithOpenings) -> MeasurementLine | None:
        leibung = self._leibung_area(room, self.DEFAULT_REVEAL_DEPTH_M)
        if leibung <= 0:
            return None

        opening_desc = ", ".join(
            f"{o.opening_type} {o.width_m}×{o.height_m} ({o.count}×)"
            for o in room.openings
        )

        return MeasurementLine(
            room_id=str(room.id),
            room_name=room.name,
            description=f"Leibungen {room.name} ({room.unit_name})",
            formula_description=f"Σ Öffnungsumfang × Leibungstiefe {self.DEFAULT_REVEAL_DEPTH_M}m",
            formula_expression=f"Öffnungen: {opening_desc}",
            raw_quantity=self._round(leibung),
            onorm_factor=Decimal("1.0"),
            onorm_rule_ref="leibung",
            onorm_paragraph="§3.5 Leibungen",
            deductions=[],
            net_quantity=self._round(leibung),
            unit="m2",
        )

    def _height_factor(self, height_m: Decimal) -> Decimal:
        """Height surcharge factor — branchenüblich für Räume > 3,20 m."""
        if height_m > Decimal("5.0"):
            return self.HEIGHT_FACTOR_OVER_500
        elif height_m > Decimal("3.2"):
            return self.HEIGHT_FACTOR_320_500
        return Decimal("1.0")
