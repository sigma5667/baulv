from datetime import datetime, date
from uuid import UUID

from pydantic import BaseModel


class ONormDokumentResponse(BaseModel):
    """Lightweight registry view of an ÖNORM entry.

    Note: ``file_path`` is intentionally absent — BauLV no longer stores
    copyrighted ÖNORM PDFs on its servers (see ``app/api/onorm.py``).
    """

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
