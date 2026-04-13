"""LV text generation using Claude API.

AI generates ÖNORM-style position texts (Langtext).
Quantities come from the deterministic calculation engine — NOT from AI.
"""

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.config import settings
from app.db.models.lv import Leistungsverzeichnis, Position
from app.onorm_rag.retriever import search_onorm_chunks

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "position_text_system.txt").read_text(encoding="utf-8")


async def generate_position_texts(lv_id: UUID, db: AsyncSession) -> int:
    """Generate AI-powered Langtext for all positions in an LV.

    Uses only the ÖNORMs selected for this LV as knowledge base.
    Returns number of positions updated.
    """
    import anthropic

    # Load LV with positions and selected ÖNORMs
    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen)
            .selectinload("positionen"),
            selectinload(Leistungsverzeichnis.selected_onorms),
        )
    )
    result = await db.execute(stmt)
    lv = result.scalars().first()
    if not lv:
        raise ValueError(f"LV {lv_id} not found")

    # Collect positions that need text
    positions_to_update: list[Position] = []
    position_descriptions = []
    for gruppe in lv.gruppen:
        for pos in gruppe.positionen:
            if not pos.is_locked and not pos.langtext:
                positions_to_update.append(pos)
                position_descriptions.append({
                    "position_code": pos.positions_nummer,
                    "kurztext": pos.kurztext,
                    "einheit": pos.einheit,
                    "menge": float(pos.menge) if pos.menge else 0,
                    "gruppe": gruppe.bezeichnung,
                })

    if not position_descriptions:
        return 0

    # Retrieve relevant ÖNORM context from selected documents only
    onorm_context = ""
    if lv.selected_onorms:
        selected_ids = [doc.id for doc in lv.selected_onorms]
        onorm_names = ", ".join(f"ÖNORM {doc.norm_nummer}" for doc in lv.selected_onorms)
        chunks = await search_onorm_chunks(
            query=lv.trade,
            db=db,
            dokument_ids=selected_ids,
            top_k=10,
        )
        if chunks:
            onorm_context = f"\n\nRelevante ÖNORM-Abschnitte ({onorm_names}):\n"
            for chunk in chunks:
                header = f"[{chunk.section_number or ''}] {chunk.section_title or ''}"
                onorm_context += f"\n{header}\n{chunk.chunk_text[:500]}\n"

    # Call Claude API
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    user_message = (
        f"Gewerk: {lv.trade}\n"
        f"ÖNORM: {lv.onorm_basis or 'nicht angegeben'}\n\n"
        f"Erstelle Langtexte für folgende Positionen:\n"
        f"{json.dumps(position_descriptions, ensure_ascii=False, indent=2)}"
        f"{onorm_context}"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # Parse response
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

    # Update positions with generated texts
    texts_by_code = {p["position_code"]: p["langtext"] for p in data.get("positions", [])}
    updated = 0
    for pos in positions_to_update:
        if pos.positions_nummer in texts_by_code:
            pos.langtext = texts_by_code[pos.positions_nummer]
            pos.text_source = "ai"
            updated += 1

    await db.flush()
    return updated
