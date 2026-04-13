from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ChatSessionCreate(BaseModel):
    project_id: UUID | None = None
    title: str | None = None


class ChatSessionResponse(BaseModel):
    id: UUID
    project_id: UUID | None
    title: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageCreate(BaseModel):
    content: str


class ChatMessageResponse(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    context_refs: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}
