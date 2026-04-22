"""Pydantic schemas for Project / Building / Floor / Unit and the
aggregate "project structure" tree.

``ProjectStructureResponse`` is what the hierarchical GET endpoint
(``GET /projects/{id}/structure``) returns: a fully-populated
Gebäude → Stockwerk → Einheit → Raum → Öffnung tree so the frontend
can render the collapsible structure page in a single request. We
re-use ``RoomResponse`` / ``OpeningResponse`` from ``schemas.room`` so
the field set stays in lock-step with the flat ``/rooms`` endpoints —
that way the UI never has to branch on "was this fetched via the tree
or via the flat list".
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.room import RoomResponse


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


# --- Buildings ---------------------------------------------------------------


class BuildingCreate(BaseModel):
    name: str
    sort_order: int = 0


class BuildingUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None


class BuildingResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    sort_order: int

    model_config = {"from_attributes": True}


# --- Floors ------------------------------------------------------------------


class FloorCreate(BaseModel):
    name: str
    level_number: int | None = None
    floor_height_m: float | None = None
    sort_order: int = 0


class FloorUpdate(BaseModel):
    name: str | None = None
    level_number: int | None = None
    floor_height_m: float | None = None
    sort_order: int | None = None


class FloorResponse(BaseModel):
    id: UUID
    building_id: UUID
    name: str
    level_number: int | None
    floor_height_m: float | None
    sort_order: int

    model_config = {"from_attributes": True}


# --- Units -------------------------------------------------------------------


class UnitCreate(BaseModel):
    name: str
    unit_type: str | None = None
    sort_order: int = 0


class UnitUpdate(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    sort_order: int | None = None


class UnitResponse(BaseModel):
    id: UUID
    floor_id: UUID
    name: str
    unit_type: str | None
    sort_order: int

    model_config = {"from_attributes": True}


# --- Aggregated structure tree -----------------------------------------------
#
# Pydantic evaluates the nested models bottom-up: Unit holds Rooms,
# Floor holds Units, Building holds Floors. Each tier sets
# ``from_attributes=True`` so we can return SQLAlchemy instances
# directly from the endpoint (``selectinload`` in the query eagerly
# loads the whole tree, so no lazy-load calls fire at serialization
# time).


class UnitWithRooms(BaseModel):
    id: UUID
    floor_id: UUID
    name: str
    unit_type: str | None
    sort_order: int
    rooms: list[RoomResponse] = []

    model_config = {"from_attributes": True}


class FloorWithUnits(BaseModel):
    id: UUID
    building_id: UUID
    name: str
    level_number: int | None
    floor_height_m: float | None
    sort_order: int
    units: list[UnitWithRooms] = []

    model_config = {"from_attributes": True}


class BuildingWithChildren(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    sort_order: int
    floors: list[FloorWithUnits] = []

    model_config = {"from_attributes": True}


class ProjectStructureResponse(BaseModel):
    """Single-shot payload for the collapsible project-tree UI."""

    project_id: UUID
    buildings: list[BuildingWithChildren] = []


# --- Quick-add ---------------------------------------------------------------


class QuickAddResponse(BaseModel):
    """Returned by the "Schnell-Anlage: Einfamilienhaus" endpoint.

    Echoes back the IDs of everything that was just created so the
    frontend can scroll to / expand the new nodes without a re-fetch
    round-trip.
    """

    project_id: UUID
    building_id: UUID
    floor_ids: list[UUID] = []
    unit_ids: list[UUID] = []
