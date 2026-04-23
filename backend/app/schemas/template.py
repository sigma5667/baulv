"""Pydantic schemas for the LV template library.

The JSONB ``template_data`` shape is validated on the way in (via
``TemplatePositionIn`` / ``TemplateGruppeIn``) so a malformed custom
template can't land in the DB and blow up at instantiation time.
On the way out we return it as a loose dict — the frontend renders
it directly — but the summary endpoint (``list_templates``) strips
the payload and returns the structure counts instead so the library
page doesn't ship hundreds of Langtext paragraphs over the wire.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Inbound template payload (either seeded from a migration or posted by a
# user via "Als Vorlage speichern"). These are lax on the way in because
# the seeded system templates are trusted and user-saved templates only
# get filled in from the server's own LV copy, not raw user input.
# ---------------------------------------------------------------------------


class TemplatePosition(BaseModel):
    """One line in a template — no menge, no einheitspreis."""

    positions_nummer: str
    kurztext: str
    langtext: str | None = None
    einheit: str
    # ``wand`` | ``decke`` | ``boden`` | ``vorarbeit`` | ``sonstiges``.
    # Informational only — the wall-area sync logic keys off the
    # kurztext keywords, not this field. Kept for future UI badges.
    kategorie: str | None = None


class TemplateGruppe(BaseModel):
    nummer: str
    bezeichnung: str
    positionen: list[TemplatePosition] = Field(default_factory=list)


class TemplateData(BaseModel):
    gruppen: list[TemplateGruppe] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API request bodies
# ---------------------------------------------------------------------------


class TemplateCreateFromLV(BaseModel):
    """Body for POST /api/templates — saves an existing LV as a template.

    The server copies the LV's gruppen/positionen into ``template_data``,
    strips ``menge`` and ``einheitspreis`` (templates are price-agnostic),
    and marks the row as a user-owned template.
    """

    lv_id: UUID
    name: str
    description: str | None = None
    category: Literal[
        "einfamilienhaus",
        "wohnanlage",
        "buero",
        "sanierung",
        "dachausbau",
        "sonstiges",
    ]


class LVFromTemplateRequest(BaseModel):
    """Body for POST /api/lv/from-template — spawns a new LV in a project."""

    project_id: UUID
    template_id: UUID
    # Optional override; defaults to the template's name when null.
    name: str | None = None


# ---------------------------------------------------------------------------
# API responses
# ---------------------------------------------------------------------------


class TemplateSummary(BaseModel):
    """Row in the templates list — no payload, just counts."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    category: str
    gewerk: str
    is_system: bool
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    # Derived on the server from ``template_data`` so the frontend can
    # show "14 Positionen · 5 Leistungsgruppen" on the card without
    # parsing the blob itself.
    gruppen_count: int = 0
    positionen_count: int = 0


class TemplateDetail(BaseModel):
    """Full template including the positions payload."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    category: str
    gewerk: str
    is_system: bool
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    template_data: dict


class LVFromTemplateResponse(BaseModel):
    """Return shape of /api/lv/from-template — minimal, lets the
    frontend navigate to the new LV without a second fetch."""

    lv_id: UUID
    project_id: UUID
    name: str
    trade: str
    gruppen_created: int
    positionen_created: int
