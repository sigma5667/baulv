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


class PlanDeletionPreview(BaseModel):
    """Counts of rows that would be removed when a plan is deleted.

    Returned by ``GET /plans/{id}/deletion-preview`` and consumed by
    the confirmation dialog in the frontend so the user knows the
    blast radius of each delete-mode choice before clicking.
    """

    plan_id: UUID
    filename: str
    rooms_linked: int = 0
    """How many rooms have ``plan_id == this_plan``. These are the
    rooms that would survive (with plan_id NULL'd) on
    ``delete_rooms=false`` or be deleted on ``delete_rooms=true``."""
    rooms_manual_among_linked: int = 0
    """Subset of ``rooms_linked`` whose ``source = 'manual'`` — i.e.
    rooms that started AI-extracted but the user has since
    edited. Surfaced separately so the dialog can warn "8 verknüpft,
    davon 3 manuell überarbeitet"."""
    openings_linked: int = 0
    """Total openings on the linked rooms — these cascade-delete
    along with their rooms when ``delete_rooms=true``."""
    proofs_linked: int = 0
    """Berechnungsnachweis rows pointing to linked rooms — these
    cascade-delete with the rooms. Counted separately because
    losing a calculation proof has bigger blast radius than losing
    an opening: an LV position keeps its cached ``menge`` but loses
    the audit trail."""


class PlanDeletionResult(BaseModel):
    """Returned by ``DELETE /plans/{id}``.

    Same shape as the preview minus ``filename`` so the caller can
    render a "X Räume und Y Berechnungsnachweise gelöscht" toast
    after the operation.
    """

    plan_id: UUID
    delete_rooms: bool
    rooms_deleted: int
    openings_deleted: int
    proofs_deleted: int
    file_unlinked: bool
    """True if the on-disk PDF file was successfully removed.
    False indicates a best-effort failure (file already gone, or
    permission error) — the DB delete still went through."""


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
