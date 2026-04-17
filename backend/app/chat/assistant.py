"""AI chat assistant with project context.

No external norm library is consulted — the calculation engine and its
built-in rules are the only authoritative reference. The assistant
answers questions in plain German construction language based on the
user's project data and what it already knows about Austrian building
practice from its training.
"""

from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models.project import Project, Building, Floor, Unit
from app.db.models.chat import ChatSession, ChatMessage


SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "construction_assistant.txt"
).read_text(encoding="utf-8")


async def chat_with_assistant(
    session_id: UUID,
    user_message: str,
    db: AsyncSession,
) -> str:
    """Process a chat message and return the assistant's reply."""
    import anthropic

    session = await db.get(ChatSession, session_id)
    if not session:
        raise ValueError(f"Chat session {session_id} not found")

    # Save user message
    db.add(
        ChatMessage(session_id=session_id, role="user", content=user_message)
    )
    await db.flush()

    # Build context (project tree only — no external norm library)
    context = await _build_context(session, db)

    # Load message history
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    claude_messages = [{"role": m.role, "content": m.content} for m in messages]

    system = SYSTEM_PROMPT
    if context:
        system += f"\n\n--- Projektkontext ---\n{context}"

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=system,
        messages=claude_messages,
    )
    assistant_text = response.content[0].text

    db.add(
        ChatMessage(
            session_id=session_id, role="assistant", content=assistant_text
        )
    )
    await db.flush()
    return assistant_text


async def _build_context(session: ChatSession, db: AsyncSession) -> str:
    """Summarize the current project's structure for the system prompt."""
    if not session.project_id:
        return ""

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
    if not project:
        return ""

    parts: list[str] = [
        f"Projekt: {project.name}",
        f"Adresse: {project.address or 'nicht angegeben'}",
    ]
    for building in project.buildings:
        for floor in building.floors:
            for unit in floor.units:
                parts.append(f"\n{unit.name} ({floor.name}):")
                for room in unit.rooms:
                    line = (
                        f"  - {room.name}: {room.area_m2}m², "
                        f"Umfang {room.perimeter_m}m, RH {room.height_m}m"
                    )
                    if room.floor_type:
                        line += f", Boden: {room.floor_type}"
                    parts.append(line)
    return "\n".join(parts)
