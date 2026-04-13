from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP

from app.calculation_engine.types import (
    RoomWithOpenings,
    OpeningData,
    MeasurementLine,
    PositionQuantity,
    DeductionDetail,
)


class TradeCalculator(ABC):
    """Abstract base class for trade-specific ÖNORM calculation modules.

    Each trade (Gewerk) implements this class with its specific
    measurement rules from the relevant ÖNORM standard.

    CRITICAL: All calculations must be deterministic and traceable.
    No AI calls, no database access, no side effects.
    """

    @property
    @abstractmethod
    def trade_name(self) -> str:
        """Internal identifier, e.g., 'malerarbeiten'."""

    @property
    @abstractmethod
    def onorm_reference(self) -> str:
        """ÖNORM standard reference, e.g., 'B 2230-1'."""

    @abstractmethod
    def calculate(self, rooms: list[RoomWithOpenings]) -> list[PositionQuantity]:
        """Run the full deterministic calculation for this trade.

        Args:
            rooms: List of rooms with dimensions and openings.

        Returns:
            List of position quantities with full calculation proof.
        """

    # --- Shared geometry helpers ---

    @staticmethod
    def _round(value: Decimal, places: int = 3) -> Decimal:
        """Round to specified decimal places using HALF_UP."""
        return value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)

    @staticmethod
    def _wall_area(room: RoomWithOpenings) -> Decimal:
        """Gross wall area: perimeter × height."""
        return room.perimeter_m * room.height_m

    @staticmethod
    def _floor_area(room: RoomWithOpenings) -> Decimal:
        """Floor area (= ceiling area for flat ceilings)."""
        return room.area_m2

    @staticmethod
    def _ceiling_area(room: RoomWithOpenings) -> Decimal:
        """Ceiling area. Same as floor area for standard rooms."""
        return room.area_m2

    @staticmethod
    def _opening_deductions(
        room: RoomWithOpenings,
        min_area: Decimal,
    ) -> tuple[Decimal, list[DeductionDetail]]:
        """Calculate opening deductions according to ÖNORM rules.

        Most ÖNORM standards specify a minimum opening area below which
        openings are NOT deducted (e.g., >2.5 m² or >5.0 m²).

        Returns:
            Tuple of (total_deduction, list_of_detail_records)
        """
        total = Decimal("0")
        details: list[DeductionDetail] = []

        for opening in room.openings:
            area = opening.total_area_m2
            if area >= min_area:
                total += area
                details.append(DeductionDetail(
                    opening=f"{opening.opening_type} {opening.width_m}×{opening.height_m} ({opening.count}×)",
                    area=float(area),
                    deducted=True,
                ))
            else:
                details.append(DeductionDetail(
                    opening=f"{opening.opening_type} {opening.width_m}×{opening.height_m} ({opening.count}×)",
                    area=float(area),
                    deducted=False,
                    reason=f"< {min_area} m² (ÖNORM Mindestfläche)",
                ))

        return total, details

    @staticmethod
    def _leibung_area(room: RoomWithOpenings, reveal_depth_m: Decimal) -> Decimal:
        """Calculate reveal (Leibung) area for all openings in a room.

        Leibung = perimeter of each opening × reveal depth.
        """
        total = Decimal("0")
        for opening in room.openings:
            perimeter = (opening.width_m + opening.height_m) * 2
            total += perimeter * reveal_depth_m * opening.count
        return total
