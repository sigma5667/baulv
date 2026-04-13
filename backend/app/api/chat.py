from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.chat import ChatSession, ChatMessage
from app.db.models.user import User
from app.schemas.chat import (
    ChatSessionCreate, ChatSessionResponse,
    ChatMessageCreate, ChatMessageResponse,
)
from app.chat.assistant import chat_with_assistant
from app.auth import get_current_user
from app.api.ownership import verify_project_owner, verify_chat_session_owner
from app.subscriptions import require_feature

router = APIRouter()


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
        response_text = await chat_with_assistant(session_id, data.content, db)
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Chat failed: {str(e)}")
