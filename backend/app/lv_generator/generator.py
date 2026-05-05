"""LV text generation using the Claude API.

Claude writes the Langtext (long description) for each position in
Austrian construction language. Quantities and unit-price placeholders
come from the deterministic calculation engine — AI is strictly for
prose.

A previous iteration retrieved chunks from an external RAG index of
construction-rule documents and injected them into the prompt. That
index has been removed; Claude now relies on its baseline knowledge
of Austrian building practice plus the trade and kurztext of each
position to produce text. If the result is wrong, the user edits it
(or locks the position) — same as before.
"""

import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.config import settings
from app.db.models.lv import Leistungsverzeichnis, Position

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "position_text_system.txt"
).read_text(encoding="utf-8")


# Current-generation Sonnet. The previous pin (``claude-sonnet-4-20250514``)
# is an older date-suffixed ID that Anthropic dropped from rotation —
# requests against it returned 404/503 and surfaced to the frontend as
# a generic 500 on AI-Texte. Keep this in lock-step with the advisor
# model in ``app/chat/assistant.py`` so both AI features go through
# the same Sonnet generation.
LV_TEXT_MODEL = "claude-sonnet-4-6"


async def generate_position_texts(lv_id: UUID, db: AsyncSession) -> int:
    """Generate Langtext for every unlocked, text-less position in an LV.

    Returns the number of positions updated.
    """
    import anthropic

    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen).selectinload("positionen"),
        )
    )
    result = await db.execute(stmt)
    lv = result.scalars().first()
    if not lv:
        raise ValueError(f"LV {lv_id} not found")

    positions_to_update: list[Position] = []
    position_descriptions: list[dict] = []
    for gruppe in lv.gruppen:
        for pos in gruppe.positionen:
            if not pos.is_locked and not pos.langtext:
                positions_to_update.append(pos)
                position_descriptions.append(
                    {
                        "position_code": pos.positions_nummer,
                        "kurztext": pos.kurztext,
                        "einheit": pos.einheit,
                        "menge": float(pos.menge) if pos.menge else 0,
                        "gruppe": gruppe.bezeichnung,
                    }
                )

    if not position_descriptions:
        return 0

    # AsyncAnthropic because this function runs inside an async request
    # handler — the sync client would block the event loop for the full
    # Claude round-trip (often 5-15s), stalling every other request on
    # this worker. Failures propagate up unwrapped so the ``lv.py`` route
    # handler can distinguish ValueError (our bugs) from SDK errors
    # (wrong model, bad key, etc.), which now get logged with the exact
    # class/status/message for Railway-side diagnosis.
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    user_message = (
        f"Gewerk: {lv.trade}\n\n"
        "Erstelle Langtexte für folgende Positionen:\n"
        f"{json.dumps(position_descriptions, ensure_ascii=False, indent=2)}"
    )
    try:
        message = await client.messages.create(
            model=LV_TEXT_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:  # noqa: BLE001 — log then re-raise
        err_class = type(e).__name__
        status_code = getattr(e, "status_code", None)
        message_attr = getattr(e, "message", None) or str(e)
        logger.error(
            "generate_position_texts.anthropic_error lv=%s model=%s "
            "class=%s status=%s message=%s",
            lv_id,
            LV_TEXT_MODEL,
            err_class,
            status_code,
            message_attr,
        )
        raise

    response_text = message.content[0].text
    try:
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0]
        else:
            json_str = response_text
        data = json.loads(json_str.strip())
    except (json.JSONDecodeError, IndexError):
        return 0

    texts_by_code = {p["position_code"]: p["langtext"] for p in data.get("positions", [])}
    updated = 0
    for pos in positions_to_update:
        if pos.positions_nummer in texts_by_code:
            pos.langtext = texts_by_code[pos.positions_nummer]
            pos.text_source = "ai"
            updated += 1
    await db.flush()
    return updated
