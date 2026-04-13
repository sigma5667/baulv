from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PlanResponse(BaseModel):
    id: UUID
    project_id: UUID
    filename: str
    file_size_bytes: int | None
    page_count: int | None
    plan_type: str | None
    analysis_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PlanAnalysisResult(BaseModel):
    """Result from Claude Vision plan analysis."""
    plan_id: UUID
    rooms_extracted: int
    rooms: list["ExtractedRoom"]


class ExtractedRoom(BaseModel):
    room_name: str
    room_number: str | None = None
    room_type: str | None = None
    area_m2: float | None = None
    perimeter_m: float | None = None
    height_m: float | None = None
    floor_type: str | None = None
    is_wet_room: bool = False
    has_dachschraege: bool = False
    is_staircase: bool = False
    confidence: float = 0.0
    openings: list["ExtractedOpening"] = []


class ExtractedOpening(BaseModel):
    opening_type: str
    width_m: float
    height_m: float
    count: int = 1
