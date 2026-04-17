from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LVCreate(BaseModel):
    name: str
    trade: str
    vorbemerkungen: str | None = None


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
    """Traceable calculation proof for a single position × room pair.

    The underlying DB columns are still named ``onorm_factor``,
    ``onorm_rule_ref``, and ``onorm_paragraph`` because they carry
    the math metadata emitted by the calculation engine. We expose
    them under generic names (``rule_factor``, ``rule_ref``,
    ``rule_paragraph``) so the frontend never sees the word "ÖNORM"
    — the application is a calculation engine, not a norm library.

    ``populate_by_name`` + ``Field(alias=...)`` means the ORM row's
    ``onorm_factor`` attribute still fills the ``rule_factor`` field
    without a manual projection layer; the response JSON uses the
    generic keys.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    position_id: UUID
    room_id: UUID
    raw_quantity: float
    formula_description: str
    formula_expression: str
    rule_factor: float = Field(alias="onorm_factor")
    rule_ref: str | None = Field(default=None, alias="onorm_rule_ref")
    rule_paragraph: str | None = Field(default=None, alias="onorm_paragraph")
    deductions: list[dict]
    net_quantity: float
    unit: str
    notes: str | None


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


class LVResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    trade: str
    status: str
    vorbemerkungen: str | None
    created_at: datetime
    updated_at: datetime
    gruppen: list[LeistungsgruppeResponse] = []

    model_config = {"from_attributes": True}
