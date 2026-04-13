from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    address: str | None = None
    client_name: str | None = None
    project_number: str | None = None
    grundstuecksnr: str | None = None
    planverfasser: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    address: str | None = None
    client_name: str | None = None
    project_number: str | None = None
    grundstuecksnr: str | None = None
    planverfasser: str | None = None
    status: str | None = None


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    address: str | None
    client_name: str | None
    project_number: str | None
    grundstuecksnr: str | None
    planverfasser: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BuildingCreate(BaseModel):
    name: str
    sort_order: int = 0


class BuildingResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    sort_order: int

    model_config = {"from_attributes": True}


class FloorCreate(BaseModel):
    name: str
    level_number: int | None = None
    floor_height_m: float | None = None
    sort_order: int = 0


class FloorResponse(BaseModel):
    id: UUID
    building_id: UUID
    name: str
    level_number: int | None
    floor_height_m: float | None
    sort_order: int

    model_config = {"from_attributes": True}


class UnitCreate(BaseModel):
    name: str
    unit_type: str | None = None
    sort_order: int = 0


class UnitResponse(BaseModel):
    id: UUID
    floor_id: UUID
    name: str
    unit_type: str | None
    sort_order: int

    model_config = {"from_attributes": True}
