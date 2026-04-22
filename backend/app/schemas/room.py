from uuid import UUID

from pydantic import BaseModel, Field


class OpeningCreate(BaseModel):
    opening_type: str
    width_m: float
    height_m: float
    count: int = 1
    description: str | None = None


class OpeningUpdate(BaseModel):
    opening_type: str | None = None
    width_m: float | None = None
    height_m: float | None = None
    count: int | None = None
    description: str | None = None


class OpeningResponse(BaseModel):
    id: UUID
    room_id: UUID
    opening_type: str
    width_m: float
    height_m: float
    count: int
    description: str | None
    source: str

    model_config = {"from_attributes": True}


class RoomCreate(BaseModel):
    name: str
    room_number: str | None = None
    room_type: str | None = None
    area_m2: float | None = None
    perimeter_m: float | None = None
    height_m: float | None = None
    # Four accepted values: schnitt / grundriss / manual / default.
    # The default (for a user-created room) is ``manual`` — the user
    # typed the height. Validation happens in the API layer against
    # the same set the pipeline uses.
    ceiling_height_source: str | None = None
    floor_type: str | None = None
    wall_type: str | None = None
    ceiling_type: str | None = None
    is_wet_room: bool = False
    has_dachschraege: bool = False
    is_staircase: bool = False
    deductions_enabled: bool = True
    openings: list[OpeningCreate] = []


class RoomUpdate(BaseModel):
    name: str | None = None
    room_number: str | None = None
    room_type: str | None = None
    area_m2: float | None = None
    perimeter_m: float | None = None
    height_m: float | None = None
    ceiling_height_source: str | None = None
    floor_type: str | None = None
    wall_type: str | None = None
    ceiling_type: str | None = None
    is_wet_room: bool | None = None
    has_dachschraege: bool | None = None
    is_staircase: bool | None = None
    deductions_enabled: bool | None = None


class RoomResponse(BaseModel):
    id: UUID
    unit_id: UUID
    plan_id: UUID | None
    name: str
    room_number: str | None
    room_type: str | None
    area_m2: float | None
    perimeter_m: float | None
    height_m: float | None
    ceiling_height_source: str
    floor_type: str | None
    wall_type: str | None
    ceiling_type: str | None
    is_wet_room: bool
    has_dachschraege: bool
    is_staircase: bool
    # Wall-calculation cache. Null until the calculation has been run
    # (freshly created manual rooms sit in this state); populated by
    # the plan-analysis pipeline on ingest and by the
    # ``/calculate-walls`` endpoints thereafter.
    wall_area_gross_m2: float | None = None
    wall_area_net_m2: float | None = None
    applied_factor: float | None = None
    deductions_enabled: bool = True
    source: str
    ai_confidence: float | None
    openings: list[OpeningResponse] = []

    model_config = {"from_attributes": True}


class WallCalculationResponse(BaseModel):
    """Returned by POST /rooms/{id}/calculate-walls and the bulk sibling.

    Kept separate from ``RoomResponse`` because the endpoints return a
    calculation-specific payload (per-room figures + which factor was
    applied + deduction count) rather than a full room row. Frontend
    callers that also need the full row can re-fetch from the list
    endpoint after the calculation succeeds.
    """

    room_id: UUID
    wall_area_gross_m2: float
    wall_area_net_m2: float
    applied_factor: float
    deductions_total_m2: float = Field(
        description="Sum of opening areas subtracted from the net value."
    )
    deductions_considered_count: int = Field(
        description="How many individual openings (post-count expansion) counted as deductions."
    )
    perimeter_m: float
    height_used_m: float
    ceiling_height_source: str


class BulkWallCalculationResponse(BaseModel):
    project_id: UUID
    rooms_calculated: int
    results: list[WallCalculationResponse]
