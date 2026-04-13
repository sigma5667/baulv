from uuid import UUID

from pydantic import BaseModel


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
    floor_type: str | None = None
    wall_type: str | None = None
    ceiling_type: str | None = None
    is_wet_room: bool = False
    has_dachschraege: bool = False
    is_staircase: bool = False
    openings: list[OpeningCreate] = []


class RoomUpdate(BaseModel):
    name: str | None = None
    room_number: str | None = None
    room_type: str | None = None
    area_m2: float | None = None
    perimeter_m: float | None = None
    height_m: float | None = None
    floor_type: str | None = None
    wall_type: str | None = None
    ceiling_type: str | None = None
    is_wet_room: bool | None = None
    has_dachschraege: bool | None = None
    is_staircase: bool | None = None


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
    floor_type: str | None
    wall_type: str | None
    ceiling_type: str | None
    is_wet_room: bool
    has_dachschraege: bool
    is_staircase: bool
    source: str
    ai_confidence: float | None
    openings: list[OpeningResponse] = []

    model_config = {"from_attributes": True}
