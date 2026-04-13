"""AI chat assistant with project context and ÖNORM knowledge."""

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models.project import Project, Building, Floor, Unit, Room
from app.db.models.lv import Leistungsverzeichnis
from app.db.models.chat import ChatSession, ChatMessage
from app.onorm_rag.retriever import search_onorm_chunks


SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "construction_assistant.txt").read_text(encoding="utf-8")


async def chat_with_assistant(
    session_id: UUID,
    user_message: str,
    db: AsyncSession,
) -> str:
    """Process a chat message and return assistant response."""
    import anthropic

    session = await db.get(ChatSession, session_id)
    if not session:
        raise ValueError(f"Chat session {session_id} not found")

    # Save user message
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=user_message,
    )
    db.add(user_msg)
    await db.flush()

    # Build context
    context = await _build_context(session, user_message, db)

    # Load message history
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    # Build Claude messages
    claude_messages = []
    for msg in messages:
        claude_messages.append({"role": msg.role, "content": msg.content})

    system = SYSTEM_PROMPT
    if context:
        system += f"\n\n--- Projektkontext ---\n{context}"

    # Call Claude
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=system,
        messages=claude_messages,
    )

    assistant_text = response.content[0].text

    # Save assistant message
    assistant_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=assistant_text,
    )
    db.add(assistant_msg)
    await db.flush()

    return assistant_text


async def _build_context(session: ChatSession, query: str, db: AsyncSession) -> str:
    """Build context string from project data and ÖNORM knowledge."""
    parts: list[str] = []

    # Project context
    if session.project_id:
        stmt = (
            select(Project)
            .where(Project.id == session.project_id)
            .options(
                selectinload(Project.buildings)
                .selectinload(Building.floors)
                .selectinload(Floor.units)
                .selectinload(Unit.rooms)
            )
        )
        result = await db.execute(stmt)
        project = result.scalars().first()

        if project:
            parts.append(f"Projekt: {project.name}")
            parts.append(f"Adresse: {project.address or 'nicht angegeben'}")

            for building in project.buildings:
                for floor in building.floors:
                    for unit in floor.units:
                        parts.append(f"\n{unit.name} ({floor.name}):")
                        for room in unit.rooms:
                            room_info = f"  - {room.name}: {room.area_m2}m², Umfang {room.perimeter_m}m, RH {room.height_m}m"
                            if room.floor_type:
                                room_info += f", Boden: {room.floor_type}"
                            parts.append(room_info)

    # Collect selected ÖNORM document IDs from project's LVs
    selected_dokument_ids = None
    if session.project_id:
        lv_stmt = (
            select(Leistungsverzeichnis)
            .where(Leistungsverzeichnis.project_id == session.project_id)
            .options(selectinload(Leistungsverzeichnis.selected_onorms))
        )
        lv_result = await db.execute(lv_stmt)
        lvs = lv_result.scalars().all()
        all_ids = set()
        for lv in lvs:
            for doc in lv.selected_onorms:
                all_ids.add(doc.id)
        if all_ids:
            selected_dokument_ids = list(all_ids)

    # ÖNORM context via RAG — scoped to selected documents if available
    relevant_chunks = await search_onorm_chunks(
        query, db, dokument_ids=selected_dokument_ids, top_k=3
    )
    if relevant_chunks:
        parts.append("\n--- Relevante ÖNORM-Abschnitte ---")
        for chunk in relevant_chunks:
            header = f"[{chunk.section_number or ''}] {chunk.section_title or ''}"
            parts.append(f"{header}\n{chunk.chunk_text[:500]}")

    return "\n".join(parts)
