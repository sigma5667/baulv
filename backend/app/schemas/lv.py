from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class LVCreate(BaseModel):
    name: str
    trade: str
    onorm_basis: str | None = None
    vorbemerkungen: str | None = None
    selected_onorm_ids: list[UUID] = []


class LVUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    vorbemerkungen: str | None = None


class PositionUpdate(BaseModel):
    kurztext: str | None = None
    langtext: str | None = None
    einheitspreis: float | None = None
    is_locked: bool | None = None


class BerechnungsnachweisResponse(BaseModel):
    id: UUID
    position_id: UUID
    room_id: UUID
    raw_quantity: float
    formula_description: str
    formula_expression: str
    onorm_factor: float
    onorm_rule_ref: str | None
    onorm_paragraph: str | None
    deductions: list[dict]
    net_quantity: float
    unit: str
    notes: str | None

    model_config = {"from_attributes": True}


class PositionResponse(BaseModel):
    id: UUID
    gruppe_id: UUID
    positions_nummer: str
    kurztext: str
    langtext: str | None
    einheit: str
    menge: float | None
    einheitspreis: float | None
    gesamtpreis: float | None
    positionsart: str
    text_source: str
    is_locked: bool
    sort_order: int
    berechnungsnachweise: list[BerechnungsnachweisResponse] = []

    model_config = {"from_attributes": True}


class LeistungsgruppeResponse(BaseModel):
    id: UUID
    lv_id: UUID
    nummer: str
    bezeichnung: str
    sort_order: int
    positionen: list[PositionResponse] = []

    model_config = {"from_attributes": True}


class ONormSelectionItem(BaseModel):
    id: UUID
    norm_nummer: str
    titel: str | None
    trade: str | None

    model_config = {"from_attributes": True}


class ONormSelectionUpdate(BaseModel):
    onorm_dokument_ids: list[UUID]


class LVResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    trade: str
    status: str
    onorm_basis: str | None
    vorbemerkungen: str | None
    created_at: datetime
    updated_at: datetime
    gruppen: list[LeistungsgruppeResponse] = []
    selected_onorms: list[ONormSelectionItem] = []

    model_config = {"from_attributes": True}
