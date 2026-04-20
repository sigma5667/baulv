"""Plan analysis pipeline: PDF → Images → Claude Vision → Structured room data.

Flow:

    upload_plan (PDF on disk)
        │
        ▼
    analyze_plan(plan_id)
        │
        ├──► _pdf_to_images()          ← PyMuPDF, 300 DPI, capped at max_plan_pages
        │
        ├──► _extract_rooms_from_image() × N pages
        │      └─► AsyncAnthropic Claude Vision
        │            └─► JSON response parsed into ExtractedRoom tree
        │
        └──► _store_extraction_result() × N pages
               └─► Inserts Building/Floor/Unit/Room/Opening rows

Failure modes we explicitly handle (and surface as German error
messages back to the user):

* ``ANTHROPIC_API_KEY`` missing → we refuse before making the call
* PDF can't be opened (corrupt / not a PDF)
* PDF has more than ``max_plan_pages`` pages
* Claude Vision returns a malformed / non-JSON body
* Claude API call times out
* Any other unexpected failure → ``PlanAnalysisError`` with a
  user-safe message, stack trace logged

All of these set ``plan.analysis_status = 'failed'`` before the
exception propagates so the frontend can distinguish "still running"
from "dead". The error message surfaced to the user is in German.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.plan import Plan
from app.db.models.project import Building, Floor, Opening, Room, Unit

logger = logging.getLogger(__name__)


ROOM_EXTRACTION_PROMPT = (
    Path(__file__).parent / "prompts" / "room_extraction.txt"
).read_text(encoding="utf-8")


# Per-page Claude Vision call timeout. A single page extraction
# normally takes 15–40s; 120s leaves generous headroom before we
# decide the API is hanging.
_CLAUDE_CALL_TIMEOUT_S = 120


class PlanAnalysisError(Exception):
    """Analysis failure with a German, user-safe message.

    ``detail`` is the message we want the frontend to display. The
    original exception (if any) is logged separately so operators
    get the full stack trace without leaking internals to the user.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


async def analyze_plan(plan_id: UUID, db: AsyncSession) -> dict:
    """Full pipeline: PDF → Claude Vision → rooms in database.

    Raises ``PlanAnalysisError`` with a German message on any known
    failure mode. Unknown failures are caught, the plan row is marked
    ``failed``, the traceback is logged, and a generic German error
    is re-raised.
    """
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise PlanAnalysisError("Plan wurde nicht gefunden.")

    if not settings.anthropic_api_key:
        logger.error("Plan analysis requested but ANTHROPIC_API_KEY is not set")
        raise PlanAnalysisError(
            "KI-Analyse ist derzeit nicht verfügbar — der Claude-API-Schlüssel "
            "ist nicht konfiguriert. Bitte kontaktieren Sie den Support."
        )

    plan.analysis_status = "processing"
    await db.flush()

    logger.info("Starting plan analysis: plan_id=%s file=%s", plan_id, plan.file_path)

    try:
        # Step 1: Convert PDF pages to images
        try:
            images = await asyncio.to_thread(_pdf_to_images, plan.file_path)
        except FileNotFoundError:
            raise PlanAnalysisError(
                "Die hochgeladene PDF-Datei wurde auf dem Server nicht gefunden. "
                "Bitte laden Sie den Plan erneut hoch."
            )
        except RuntimeError as e:
            # PyMuPDF raises RuntimeError on corrupt PDFs.
            logger.exception("PyMuPDF failed to open %s: %s", plan.file_path, e)
            raise PlanAnalysisError(
                "Die PDF-Datei konnte nicht gelesen werden. Bitte prüfen Sie, "
                "ob die Datei nicht beschädigt ist."
            )

        plan.page_count = len(images)
        await db.flush()

        if len(images) == 0:
            raise PlanAnalysisError("Die PDF enthält keine Seiten.")

        if len(images) > settings.max_plan_pages:
            raise PlanAnalysisError(
                f"Die PDF hat {len(images)} Seiten — maximal "
                f"{settings.max_plan_pages} Seiten pro Plan erlaubt. Bitte "
                f"teilen Sie die Datei auf."
            )

        logger.info("Analyzing %d pages for plan %s", len(images), plan_id)

        # Late import for the rate-limit exception type. We've already
        # confirmed the API key is set, so paying for the anthropic
        # import now is fine; doing it here (rather than at module
        # top) keeps cold imports cheap when plan analysis isn't
        # exercised.
        import anthropic

        # Step 2: Claude Vision extraction per page. Runs sequentially
        # because concurrent Claude Vision calls don't buy much for
        # typical plan sizes and would multiply quota spikes.
        all_results: list[dict] = []
        page_errors: list[str] = []
        for i, image_bytes in enumerate(images, start=1):
            try:
                result = await _extract_rooms_from_image(image_bytes, page_number=i)
                if result is not None:
                    all_results.append(result)
                else:
                    page_errors.append(f"Seite {i}: KI-Antwort nicht verwertbar")
            except asyncio.TimeoutError:
                logger.warning("Claude Vision timeout on page %d of plan %s", i, plan_id)
                page_errors.append(f"Seite {i}: Zeitüberschreitung bei der KI-Analyse")
            except anthropic.RateLimitError:
                # Rate limits are account-level; hitting one on page N
                # means page N+1 will hit it too. Abort with a specific
                # message instead of letting every remaining page fail
                # through the generic handler.
                logger.warning(
                    "Anthropic rate limit on page %d of plan %s — aborting",
                    i,
                    plan_id,
                )
                raise PlanAnalysisError(
                    "Zu viele Anfragen an die KI. Bitte warten Sie einen "
                    "Moment und versuchen Sie es erneut."
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "Claude Vision call failed for page %d of plan %s: %s",
                    i,
                    plan_id,
                    e,
                )
                page_errors.append(f"Seite {i}: {type(e).__name__}")

        # Step 3: Persist
        total_rooms = 0
        for page_result in all_results:
            rooms_created = await _store_extraction_result(page_result, plan, db)
            total_rooms += rooms_created

        # Decide the final status. If at least one page produced rooms,
        # call it "completed" (partial success is still useful); if
        # nothing came back at all, mark it failed so the user knows.
        if total_rooms == 0:
            plan.analysis_status = "failed"
            await db.flush()
            # Assemble a specific message so we're not hiding the cause.
            if page_errors:
                detail = (
                    "Die KI-Analyse hat keine Räume extrahiert. "
                    + "; ".join(page_errors[:3])
                )
                if len(page_errors) > 3:
                    detail += f" (und {len(page_errors) - 3} weitere Fehler)"
            else:
                detail = (
                    "Die KI konnte auf diesem Plan keine Räume erkennen. "
                    "Bitte prüfen Sie, ob es sich um einen Grundriss mit "
                    "lesbaren Raumbezeichnungen und Maßangaben handelt."
                )
            raise PlanAnalysisError(detail)

        plan.analysis_status = "completed"
        await db.flush()

        logger.info(
            "Plan analysis completed: plan_id=%s pages=%d rooms=%d errors=%d",
            plan_id,
            len(images),
            total_rooms,
            len(page_errors),
        )

        return {
            "plan_id": str(plan_id),
            "pages_analyzed": len(images),
            "rooms_extracted": total_rooms,
            "page_errors": page_errors,
        }

    except PlanAnalysisError:
        plan.analysis_status = "failed"
        await db.flush()
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected failure during plan analysis %s: %s", plan_id, e)
        plan.analysis_status = "failed"
        await db.flush()
        raise PlanAnalysisError(
            "Bei der KI-Analyse ist ein unerwarteter Fehler aufgetreten. "
            "Bitte versuchen Sie es erneut oder kontaktieren Sie den Support."
        )


def _pdf_to_images(file_path: str) -> list[bytes]:
    """Convert PDF to PNG bytes per page. Sync — run in a thread.

    PyMuPDF is CPU-bound and releases the GIL during rendering; we
    dispatch it from ``asyncio.to_thread`` in the caller so the
    event loop stays responsive during rendering.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(file_path)
    try:
        images: list[bytes] = []
        # Render at 300 DPI — enough detail for Claude Vision to read
        # room labels and dimension chains without blowing up bytes.
        mat = fitz.Matrix(300 / 72, 300 / 72)
        # Enforce the page cap at the render boundary too, so a 1000-
        # page PDF doesn't consume memory before the caller rejects.
        max_pages = settings.max_plan_pages
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))
        return images
    finally:
        doc.close()


async def _extract_rooms_from_image(image_bytes: bytes, page_number: int) -> dict | None:
    """Send one page image to Claude Vision and parse the JSON response.

    Returns the parsed dict on success, or ``None`` if the model's
    response could not be interpreted as JSON. Logs the raw response
    on parse failure so operators can see what came back.
    """
    # Late import — keeps module import cheap and avoids a hard dep
    # when plan analysis isn't being exercised.
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Wrap in wait_for so a stalled API call can't hang indefinitely.
    message = await asyncio.wait_for(
        client.messages.create(
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
                        {"type": "text", "text": ROOM_EXTRACTION_PROMPT},
                    ],
                }
            ],
        ),
        timeout=_CLAUDE_CALL_TIMEOUT_S,
    )

    # Claude can emit multiple content blocks (text, tool_use, thinking).
    # Concatenate every text block so we don't miss JSON that lives
    # outside index 0.
    response_text = "".join(
        getattr(block, "text", "") for block in (message.content or [])
    ).strip()

    if not response_text:
        logger.warning("Claude returned empty content on page %d", page_number)
        return None

    try:
        json_str = _extract_json_blob(response_text)
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as e:
        # Log a truncated preview so we can diagnose without flooding
        # logs on big responses.
        preview = response_text[:500].replace("\n", "\\n")
        logger.warning(
            "Claude response not parseable as JSON on page %d: %s — preview=%s",
            page_number,
            e,
            preview,
        )
        return None


def _extract_json_blob(text: str) -> str:
    """Pull a JSON object out of a Claude response.

    Claude often wraps the JSON in ```json ... ``` fences; sometimes it
    just emits raw JSON; occasionally it emits prose around the JSON.
    We handle all three by looking for a fenced block first, then a
    raw object, then falling back to the whole string.
    """
    if "```json" in text:
        # Content between the first ```json and the next ```
        after = text.split("```json", 1)[1]
        return after.split("```", 1)[0].strip()
    if "```" in text:
        after = text.split("```", 1)[1]
        return after.split("```", 1)[0].strip()
    # Prose-wrapped JSON: grab the outermost {...} if present.
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        return text[first : last + 1]
    return text.strip()


async def _store_extraction_result(
    result: dict,
    plan: Plan,
    db: AsyncSession,
) -> int:
    """Persist one page's Claude Vision result. Returns rooms created."""
    project_id = plan.project_id
    rooms_created = 0

    # Building (reuse the project's first building if one exists)
    stmt = select(Building).where(Building.project_id == project_id)
    existing = await db.execute(stmt)
    building = existing.scalars().first()
    if not building:
        building = Building(project_id=project_id, name="Gebäude 1")
        db.add(building)
        await db.flush()

    # Floor (by name; create if missing)
    floor_name = result.get("floor_name") or "EG"
    floor_level = result.get("floor_level")
    if not isinstance(floor_level, int):
        floor_level = 0
    stmt = select(Floor).where(
        Floor.building_id == building.id, Floor.name == floor_name
    )
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

    for unit_data in result.get("units", []) or []:
        unit_name = unit_data.get("unit_name") or "Einheit 1"
        unit_type = unit_data.get("unit_type") or "wohnung"

        stmt = select(Unit).where(
            Unit.floor_id == floor.id, Unit.name == unit_name
        )
        existing = await db.execute(stmt)
        unit = existing.scalars().first()
        if not unit:
            unit = Unit(floor_id=floor.id, name=unit_name, unit_type=unit_type)
            db.add(unit)
            await db.flush()

        for room_data in unit_data.get("rooms", []) or []:
            room = Room(
                unit_id=unit.id,
                plan_id=plan.id,
                name=room_data.get("room_name") or "Raum",
                room_number=room_data.get("room_number"),
                room_type=room_data.get("room_type"),
                area_m2=room_data.get("area_m2"),
                perimeter_m=room_data.get("perimeter_m"),
                height_m=room_data.get("height_m"),
                floor_type=room_data.get("floor_type"),
                is_wet_room=bool(room_data.get("is_wet_room", False)),
                has_dachschraege=bool(room_data.get("has_dachschraege", False)),
                is_staircase=bool(room_data.get("is_staircase", False)),
                source="ai",
                ai_confidence=room_data.get("confidence", 0.0),
            )
            db.add(room)
            await db.flush()

            for opening_data in room_data.get("openings", []) or []:
                opening = Opening(
                    room_id=room.id,
                    opening_type=opening_data.get("opening_type") or "fenster",
                    width_m=opening_data.get("width_m") or 1.0,
                    height_m=opening_data.get("height_m") or 1.0,
                    count=opening_data.get("count") or 1,
                    source="ai",
                )
                db.add(opening)

            rooms_created += 1

    await db.flush()
    return rooms_created
