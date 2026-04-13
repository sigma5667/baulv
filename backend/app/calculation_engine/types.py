from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID


@dataclass
class RoomWithOpenings:
    """Room data as input to the calculation engine."""
    id: UUID
    name: str
    room_type: str | None
    area_m2: Decimal
    perimeter_m: Decimal
    height_m: Decimal
    floor_type: str | None
    wall_type: str | None
    ceiling_type: str | None
    is_wet_room: bool
    has_dachschraege: bool
    is_staircase: bool
    unit_name: str = ""
    floor_name: str = ""
    openings: list["OpeningData"] = field(default_factory=list)


@dataclass
class OpeningData:
    """Opening (door/window) data for calculation."""
    opening_type: str
    width_m: Decimal
    height_m: Decimal
    count: int = 1

    @property
    def total_area_m2(self) -> Decimal:
        return self.width_m * self.height_m * self.count


@dataclass
class DeductionDetail:
    """Detail record for an opening deduction."""
    opening: str
    area: float
    deducted: bool
    reason: str = ""


@dataclass
class MeasurementLine:
    """A single line in the calculation proof (Berechnungsnachweis).

    This is the core traceability unit: for every quantity in the LV,
    there must be one MeasurementLine per room showing exactly how
    the number was derived.
    """
    room_id: str
    room_name: str
    description: str
    formula_description: str
    formula_expression: str
    raw_quantity: Decimal
    onorm_factor: Decimal
    onorm_rule_ref: str
    onorm_paragraph: str
    deductions: list[DeductionDetail] = field(default_factory=list)
    net_quantity: Decimal = Decimal("0")
    unit: str = "m2"
    notes: str = ""


@dataclass
class PositionQuantity:
    """Calculated quantity for one LV position, aggregated across rooms."""
    position_code: str
    short_text: str
    unit: str
    total_quantity: Decimal
    measurement_lines: list[MeasurementLine] = field(default_factory=list)
    gruppe_name: str = ""
    gruppe_nummer: str = ""
