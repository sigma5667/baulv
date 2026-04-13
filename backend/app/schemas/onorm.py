from datetime import datetime, date
from uuid import UUID

from pydantic import BaseModel


class ONormDokumentResponse(BaseModel):
    id: UUID
    norm_nummer: str
    titel: str | None
    trade: str | None
    ausgabe_datum: date | None
    upload_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ONormRegelResponse(BaseModel):
    id: UUID
    regel_code: str
    trade: str
    category: str | None
    description_de: str
    formula_type: str | None
    parameters: dict
    onorm_reference: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class ONormSearchRequest(BaseModel):
    query: str
    norm_nummer: str | None = None
    top_k: int = 5


class ONormChunkResponse(BaseModel):
    id: UUID
    dokument_id: UUID
    chunk_text: str
    section_number: str | None
    section_title: str | None
    page_number: int | None

    model_config = {"from_attributes": True}
