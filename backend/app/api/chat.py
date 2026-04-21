import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.db.models.chat import ChatSession, ChatMessage
from app.db.models.user import User
from app.schemas.chat import (
    ChatSessionCreate, ChatSessionResponse,
    ChatMessageCreate, ChatMessageResponse,
)
from app.chat.assistant import chat_with_assistant, ChatConfigurationError
from app.auth import get_current_user
from app.api.ownership import verify_project_owner, verify_chat_session_owner
from app.subscriptions import require_feature

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatSessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


@router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_session(
    data: ChatSessionCreate,
    user: User = Depends(require_feature("ai_chat")),
    db: AsyncSession = Depends(get_db),
):
    """Create a chat session — requires Pro plan."""
    if data.project_id:
        await verify_project_owner(data.project_id, user, db)
    session = ChatSession(**data.model_dump())
    db.add(session)
    await db.flush()
    return session


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    project_id: UUID = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if project_id:
        await verify_project_owner(project_id, user, db)
    stmt = select(ChatSession)
    if project_id:
        stmt = stmt.where(ChatSession.project_id == project_id)
    stmt = stmt.order_by(ChatSession.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
async def rename_session(
    session_id: UUID,
    data: ChatSessionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a chat session (sidebar conversation titles)."""
    await verify_chat_session_owner(session_id, user, db)
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(404, "Chat-Session nicht gefunden.")
    session.title = data.title
    await db.flush()
    await db.refresh(session)
    return session


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session and all its messages."""
    await verify_chat_session_owner(session_id, user, db)
    # Messages cascade via the SQLAlchemy relationship, but we're
    # explicit so a misconfigured cascade doesn't leave orphans.
    await db.execute(
        delete(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    await db.execute(delete(ChatSession).where(ChatSession.id == session_id))
    await db.flush()
    return None


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
async def get_messages(
    session_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_chat_session_owner(session_id, user, db)
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    return result.scalars().all()


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    session_id: UUID,
    data: ChatMessageCreate,
    user: User = Depends(require_feature("ai_chat")),
    db: AsyncSession = Depends(get_db),
):
    """Send a message and get AI response — requires Pro plan."""
    await verify_chat_session_owner(session_id, user, db)
    try:
        await chat_with_assistant(session_id, data.content, db)
        # Return the just-persisted assistant reply so the frontend
        # doesn't need a follow-up round-trip.
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        reply = result.scalars().first()
        if reply is None:
            raise HTTPException(500, "Antwort des KI-Beraters konnte nicht gespeichert werden.")
        return reply
    except ValueError as e:
        raise HTTPException(404, str(e))
    except ChatConfigurationError as e:
        # Surfaced plain-German to the frontend so the UI can display
        # it verbatim — this is the signal "set ANTHROPIC_API_KEY".
        raise HTTPException(503, str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("chat_with_assistant failed: %s", e)
        raise HTTPException(
            500,
            "Der KI-Berater ist derzeit nicht erreichbar. Bitte versuchen "
            "Sie es in ein paar Minuten erneut.",
        )
