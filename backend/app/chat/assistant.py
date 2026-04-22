"""AI chat assistant with project context.

No external norm library is consulted — the calculation engine and its
built-in rules are the only authoritative reference. The assistant
answers questions in plain German construction language based on the
user's project data and what it already knows about Austrian building
practice from its training.

Implementation notes:
  - Async Anthropic client (the route handler is async; the sync
    client would block the event loop).
  - The system prompt is cached ephemerally so repeated turns in a
    long session re-use the cached prefix (~90 % cheaper after the
    first request).
  - Failures are reported with a clear German message the UI can show
    verbatim. The ROOT CAUSE is always in Railway logs — we log the
    exact Anthropic SDK exception class, status code, and message so
    an operator can distinguish a bad API key from a bad model ID.
"""

import logging
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models.project import Project, Building, Floor, Unit
from app.db.models.chat import ChatSession, ChatMessage

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "construction_assistant.txt"
).read_text(encoding="utf-8")


# Model used for the in-app advisor. Kept at module scope so every
# log line shows which model we attempted — a 404 here is a diagnostic
# jackpot.
#
# Sonnet 4.6 is the current Sonnet generation; the former
# ``claude-sonnet-4-5`` ID fell out of rotation and was returning
# persistent 503s. The in-app advisor reasons about construction
# project context so Sonnet-tier capability is the right floor (Haiku
# would skimp on the nuanced ÖNORM-style advice the German prompt
# elicits). If a future regression points at the model ID, bump this
# to ``claude-opus-4-7`` — same API shape.
ADVISOR_MODEL = "claude-sonnet-4-6"


# Sentinel so callers can recognize configuration failures versus
# transient Claude errors. Caught in the API layer and mapped to a
# dedicated German error message.
class ChatConfigurationError(RuntimeError):
    pass


class ChatAnthropicError(RuntimeError):
    """Raised when the Anthropic SDK returns an error we want the API
    layer to surface as a 502/503 with a clean German message. The
    root-cause string is available on ``self.args[0]`` for logging;
    we keep the user-facing message generic."""


async def chat_with_assistant(
    session_id: UUID,
    user_message: str,
    db: AsyncSession,
) -> str:
    """Process a chat message and return the assistant's reply."""
    if not (settings.anthropic_api_key or "").strip():
        logger.error(
            "chat_with_assistant.no_api_key — ANTHROPIC_API_KEY is not set"
        )
        raise ChatConfigurationError(
            "Der KI-Berater ist auf diesem Server nicht konfiguriert "
            "(ANTHROPIC_API_KEY fehlt). Bitte kontaktieren Sie den "
            "Administrator."
        )

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

    # Build the system prompt as a list so we can attach cache_control
    # to the stable prefix. The project context is appended as a second
    # block and NOT cached — it changes as rooms get added/edited, and
    # caching a volatile block would invalidate the prefix every turn.
    system_blocks: list[dict] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if context:
        system_blocks.append(
            {"type": "text", "text": f"\n\n--- Projektkontext ---\n{context}"}
        )

    logger.info(
        "chat_with_assistant.calling_claude session=%s model=%s msgs=%d "
        "system_chars=%d has_project_context=%s",
        session_id,
        ADVISOR_MODEL,
        len(claude_messages),
        len(SYSTEM_PROMPT),
        bool(context),
    )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        started = time.time()
        response = await client.messages.create(
            model=ADVISOR_MODEL,
            max_tokens=2000,
            system=system_blocks,
            messages=claude_messages,
        )
        elapsed_ms = int((time.time() - started) * 1000)

        usage = getattr(response, "usage", None)
        logger.info(
            "chat_with_assistant.claude_ok session=%s stop_reason=%s "
            "elapsed_ms=%d input_tokens=%s output_tokens=%s "
            "cache_read=%s cache_write=%s",
            session_id,
            getattr(response, "stop_reason", None),
            elapsed_ms,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            getattr(usage, "cache_read_input_tokens", None),
            getattr(usage, "cache_creation_input_tokens", None),
        )
    except Exception as e:  # noqa: BLE001 — log then re-raise as typed
        err_class = type(e).__name__
        status_code = getattr(e, "status_code", None)
        body = getattr(e, "body", None)
        message = getattr(e, "message", None) or str(e)
        logger.error(
            "chat_with_assistant.anthropic_error session=%s class=%s "
            "status=%s message=%s body=%r",
            session_id,
            err_class,
            status_code,
            message,
            body,
        )
        raise ChatAnthropicError(f"{err_class}: {message}") from e

    assistant_text = response.content[0].text if response.content else ""
    if not assistant_text.strip():
        # Extremely rare, but we want deterministic behavior: persist a
        # placeholder so the message list stays coherent, and surface a
        # clear German hint to the user.
        logger.warning(
            "chat_with_assistant.empty_reply session=%s stop_reason=%s",
            session_id,
            getattr(response, "stop_reason", None),
        )
        assistant_text = (
            "Entschuldigung, darauf kann ich gerade keine sinnvolle Antwort "
            "geben. Bitte formulieren Sie die Frage anders."
        )

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
