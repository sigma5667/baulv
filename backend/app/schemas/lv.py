from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class LVCreate(BaseModel):
    name: str
    trade: str
    vorbemerkungen: str | None = None


class LVUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    vorbemerkungen: str | None = None


class PositionUpdate(BaseModel):
    """Partial update payload for a single position.

    All fields optional → caller PATCH-style sends only what changed.
    ``menge`` is exposed for the v23.5 inline-edit flow so reviewers
    can override calculated quantities without re-running the engine
    (e.g. when the AI math is right but the room input was wrong and
    re-running would be slower than typing the corrected value).
    Locked positions reject every field — that gate lives at the
    endpoint layer so all writes go through one check, not five.
    """

    kurztext: str | None = None
    langtext: str | None = None
    menge: float | None = None
    einheitspreis: float | None = None
    is_locked: bool | None = None


class BerechnungsnachweisResponse(BaseModel):
    """Traceable calculation proof for a single position × room pair.

    The underlying DB columns retain their legacy ``onorm_*`` names
    because renaming them would trigger an unnecessary migration and
    the names are nowhere user-visible. We expose them under generic
    public names (``rule_factor``, ``rule_ref``, ``rule_paragraph``)
    so the API surface stays norm-neutral — the application is a
    calculation engine, not a standards reference.

    ``validation_alias`` means the ORM row's ``onorm_factor`` attribute
    still fills the ``rule_factor`` field without a manual projection
    layer, while the response JSON uses the generic keys exclusively.

    It must be ``validation_alias``, not ``alias``: FastAPI serialises
    responses ``by_alias=True``, so a plain ``alias`` put ``onorm_factor``
    back on the wire. The frontend reads ``rule_factor`` and crashed on
    ``undefined.toFixed`` when a position with Nachweis rows was expanded
    (Live-Befund 2026-11-24).
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    position_id: UUID
    room_id: UUID
    raw_quantity: float
    formula_description: str
    formula_expression: str
    rule_factor: float = Field(validation_alias=AliasChoices("onorm_factor", "rule_factor"))
    rule_ref: str | None = Field(default=None, validation_alias=AliasChoices("onorm_rule_ref", "rule_ref"))
    rule_paragraph: str | None = Field(default=None, validation_alias=AliasChoices("onorm_paragraph", "rule_paragraph"))
    deductions: list[dict]
    net_quantity: float
    unit: str
    notes: str | None
    # Anzeige (2026-11): Raumname + Wohnungsbezeichnung ("TOP 5") — leer,
    # wenn nicht geladen bzw. keine echte TOP erkennbar.
    room_name: str | None = None
    unit_label: str | None = None


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
