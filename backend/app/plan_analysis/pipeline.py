"""Plan analysis pipeline: PDF → Images → Claude Vision → Structured room data."""

import json
import base64
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.plan import Plan
from app.db.models.project import Building, Floor, Unit, Room, Opening
from app.schemas.plan import ExtractedRoom, ExtractedOpening


ROOM_EXTRACTION_PROMPT = (Path(__file__).parent / "prompts" / "room_extraction.txt").read_text(encoding="utf-8")


async def analyze_plan(plan_id: UUID, db: AsyncSession) -> dict:
    """Full pipeline: PDF → Claude Vision → rooms in database.

    Returns summary of extraction results.
    """
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise ValueError(f"Plan {plan_id} not found")

    plan.analysis_status = "processing"
    await db.flush()

    try:
        # Step 1: Convert PDF pages to images
        images = await _pdf_to_images(plan.file_path)
        plan.page_count = len(images)

        # Step 2: Send each page to Claude Vision
        all_results = []
        for i, image_bytes in enumerate(images):
            result = await _extract_rooms_from_image(image_bytes, page_number=i + 1)
            if result:
                all_results.append(result)

        # Step 3: Store extracted data in database
        total_rooms = 0
        for page_result in all_results:
            rooms_created = await _store_extraction_result(
                page_result, plan, db
            )
            total_rooms += rooms_created

        plan.analysis_status = "completed"
        await db.flush()

        return {
            "plan_id": str(plan_id),
            "pages_analyzed": len(images),
            "rooms_extracted": total_rooms,
        }

    except Exception as e:
        plan.analysis_status = "failed"
        await db.flush()
        raise


async def _pdf_to_images(file_path: str) -> list[bytes]:
    """Convert PDF to list of page images as PNG bytes."""
    import fitz  # PyMuPDF

    doc = fitz.open(file_path)
    images = []
    for page in doc:
        # Render at 300 DPI for good quality
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


async def _extract_rooms_from_image(image_bytes: bytes, page_number: int) -> dict | None:
    """Send image to Claude Vision API and extract structured room data."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": ROOM_EXTRACTION_PROMPT,
                    },
                ],
            }
        ],
    )

    # Parse the JSON response
    response_text = message.content[0].text
    # Try to extract JSON from the response
    try:
        # Handle case where response has markdown code blocks
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0]
        else:
            json_str = response_text

        return json.loads(json_str.strip())
    except (json.JSONDecodeError, IndexError):
        return None


async def _store_extraction_result(
    result: dict,
    plan: Plan,
    db: AsyncSession,
) -> int:
    """Store Claude Vision extraction result in the database."""
    project_id = plan.project_id
    rooms_created = 0

    # Get or create building
    from sqlalchemy import select
    stmt = select(Building).where(Building.project_id == project_id)
    existing = await db.execute(stmt)
    building = existing.scalars().first()
    if not building:
        building = Building(project_id=project_id, name="Gebäude 1")
        db.add(building)
        await db.flush()

    # Get or create floor
    floor_name = result.get("floor_name", "EG")
    floor_level = result.get("floor_level", 0)
    stmt = select(Floor).where(Floor.building_id == building.id, Floor.name == floor_name)
    existing = await db.execute(stmt)
    floor = existing.scalars().first()
    if not floor:
        floor = Floor(
            building_id=building.id,
            name=floor_name,
            level_number=floor_level,
        )
        db.add(floor)
        await db.flush()

    # Process units
    for unit_data in result.get("units", []):
        unit_name = unit_data.get("unit_name", "Einheit 1")
        unit_type = unit_data.get("unit_type", "wohnung")

        stmt = select(Unit).where(Unit.floor_id == floor.id, Unit.name == unit_name)
        existing = await db.execute(stmt)
        unit = existing.scalars().first()
        if not unit:
            unit = Unit(floor_id=floor.id, name=unit_name, unit_type=unit_type)
            db.add(unit)
            await db.flush()

        # Process rooms
        for room_data in unit_data.get("rooms", []):
            room = Room(
                unit_id=unit.id,
                plan_id=plan.id,
                name=room_data.get("room_name", "Raum"),
                room_number=room_data.get("room_number"),
                room_type=room_data.get("room_type"),
                area_m2=room_data.get("area_m2"),
                perimeter_m=room_data.get("perimeter_m"),
                height_m=room_data.get("height_m"),
                floor_type=room_data.get("floor_type"),
                is_wet_room=room_data.get("is_wet_room", False),
                has_dachschraege=room_data.get("has_dachschraege", False),
                is_staircase=room_data.get("is_staircase", False),
                source="ai",
                ai_confidence=room_data.get("confidence", 0.0),
            )
            db.add(room)
            await db.flush()

            # Add openings
            for opening_data in room_data.get("openings", []):
                opening = Opening(
                    room_id=room.id,
                    opening_type=opening_data.get("opening_type", "fenster"),
                    width_m=opening_data.get("width_m", 1.0),
                    height_m=opening_data.get("height_m", 1.0),
                    count=opening_data.get("count", 1),
                    source="ai",
                )
                db.add(opening)

            rooms_created += 1

    await db.flush()
    return rooms_created
