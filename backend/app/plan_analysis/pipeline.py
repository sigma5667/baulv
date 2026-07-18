"""Plan analysis pipeline: PDF → Images → Claude Vision → Structured room data.

Flow (v24.4.6, Two-Pass-Raumerkennung):

    upload_plan (PDF on disk)
        │
        ▼
    analyze_plan(plan_id)
        │  (öffnet PDF einmal im fitz_executor, hält's offen)
        │
        ├──► _render_all_pages(doc)    ← PyMuPDF, DPI-Ladder, Pre-Render
        │                                  als Fallback-Bytes pro Seite
        │
        ├──► _extract_rooms_two_pass(doc, page_number, …) × N pages
        │      │
        │      ├──► _render_page_long_edge_jpeg → 1536-px-Probe
        │      ├──► _find_building_bbox        ← Haiku-Call (Schritt 1)
        │      ├──► _bbox_fail_safe_reason     ← Fallback-Entscheidung
        │      │      └─► bei Fail-Safe: _extract_rooms_from_image
        │      │              auf fallback_bytes (= alter Single-Pass)
        │      ├──► _render_page_clip_jpeg     ← High-DPI-Crop (Schritt 2)
        │      ├──► _should_tile? → ggf. _tile_2x2_pillow
        │      └──► _extract_rooms_from_image  ← Sonnet (Schritt 3)
        │              └─► JSON response parsed into ExtractedRoom tree
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
import concurrent.futures
import io
import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.plan import Plan
from app.db.models.project import Building, Floor, Opening, Room, Unit
from app.services.floor_covering import normalise_floor_covering
from app.services.wall_calculator import (
    OpeningInput,
    calculate_wall_areas,
    estimate_perimeter_from_area,
)


# Accepted values for the ceiling_height_source column AS CLAIMED BY
# VISION. Any other string the model hands back collapses to
# "default" — the frontend's amber warning treats that as "user
# please confirm". Deliberately does NOT contain ``floor`` (v24.6):
# that marker is minted exclusively by the backend when a room
# inherits the Stockwerk's Geschoss-Höhe (see the fan-out branch in
# ``_store_extraction_result`` and rooms.py, whose API-facing set
# DOES accept it). A hallucinated Vision ``floor`` would otherwise
# tag a real extracted height as an overwritable placeholder.
_CEILING_SOURCE_VALUES = {"schnitt", "grundriss", "manual", "default"}


# Accepted values Vision is allowed to set for ``perimeter_source``.
# ``labeled`` and ``computed`` are the v22.3 prompt-v2 values:
#   ``labeled``   — Vision read the inline perimeter label printed
#                   beside the area on the architect's plan
#                   (highest AI confidence — direct CAD output).
#   ``computed``  — Vision summed the dimension-chain along the
#                   walls itself (medium confidence — Vision's own
#                   measurement).
# Everything else Vision returns in this field collapses to the
# legacy ``vision`` tag so a partial prompt-v2 deployment doesn't
# leave us with stray values like ``"unknown"`` or empty strings in
# the column.
_VISION_PERIMETER_SOURCE_VALUES = {"labeled", "computed"}


def _coerce_positive_int(value: object) -> int | None:
    """Return ``value`` as a positive int, or None if it isn't.

    Used to validate the four pin-coordinate fields Vision returns
    in v23.1 (``position_x``, ``position_y``, ``bbox_width``,
    ``bbox_height``). Vision sometimes hallucinates negative numbers
    or non-numeric placeholders ("?", "n/a") — we treat anything
    that isn't a strictly-positive integer as "not given" rather
    than persisting nonsense that would render off-canvas in the
    Phase 2 pin viewer.

    ``0`` is also rejected because (0, 0) is the top-left corner of
    the rendered image; it's the most common Vision fallback for
    "I don't know" and pin-rendering against it would be a stack
    of pins in the corner. Better to drop the value and skip the pin.
    """
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


def _resolve_perimeter(
    extracted_perimeter: float | int | None,
    extracted_area: float | int | None,
    extracted_source: str | None = None,
) -> tuple[float | None, str | None]:
    """Pick a perimeter for a freshly extracted room + label its source.

    Three branches, in priority order:

    1. Vision returned a positive perimeter — trust it. Tag with the
       Vision-supplied ``perimeter_source`` if it's one of the
       accepted v22.3 values (``labeled`` / ``computed``); otherwise
       fall back to the legacy ``vision`` tag so we can tell pre-v22.3
       extractions apart from post-v22.3 ones.
    2. Vision returned no perimeter but a positive area — fall back
       to ``estimate_perimeter_from_area``. Tag ``estimated``.
    3. Neither — leave both ``None``. The frontend renders the red
       "Bitte eintragen" emergency-fallback so the gap is impossible
       to overlook.

    The actual estimation math lives in
    ``app.services.wall_calculator.estimate_perimeter_from_area`` so
    every entry point (this pipeline, the manual create-room
    endpoint, the recalc helper, migration 016) shares the formula.
    """
    if extracted_perimeter is not None and float(extracted_perimeter) > 0:
        if extracted_source in _VISION_PERIMETER_SOURCE_VALUES:
            return float(extracted_perimeter), extracted_source
        return float(extracted_perimeter), "vision"
    estimated = estimate_perimeter_from_area(extracted_area)
    if estimated is not None:
        return estimated, "estimated"
    return None, None


logger = logging.getLogger(__name__)


ROOM_EXTRACTION_PROMPT = (
    Path(__file__).parent / "prompts" / "room_extraction.txt"
).read_text(encoding="utf-8")


# v24.4.6 — Schritt-1-Prompt (Haiku-BBox-Probe) für den Two-Pass-Flow.
# Template-String: ``image_width`` / ``image_height`` werden zur
# Laufzeit per ``.format()`` injiziert, weil das Vision-Modell sonst
# die genauen Bildmaße aus dem Bild selbst herauslesen müsste — und
# dabei gelegentlich daneben liegt.
BUILDING_BBOX_PROMPT_TEMPLATE = (
    Path(__file__).parent / "prompts" / "building_bbox.txt"
).read_text(encoding="utf-8")


# Per-page Claude Vision call timeout. A single page extraction
# normally takes 15–40s; 120s leaves generous headroom before we
# decide the API is hanging.
_CLAUDE_CALL_TIMEOUT_S = 120


# Output-token cap for one Vision response. Bumped from 4096 to
# 8192 in v23.1.1 after a 130-room plan started returning
# ``BadRequestError`` from the Anthropic API: with the v23.1
# pin-coordinate fields each room gained ~30 output tokens, and a
# multi-page plan with 20+ rooms per page edged close enough to
# 4096 that the request started getting rejected with a
# "max_tokens insufficient for expected output" 400. 8192 leaves
# generous headroom; we only pay for what's actually generated.
_VISION_MAX_TOKENS = 8192


def _translate_anthropic_error(exc: Exception) -> str | None:
    """Map a recognised Anthropic API error to a German user message.

    Returns ``None`` for errors we don't have a friendly translation
    for — the caller falls back to the diagnostic ``ClassName —
    message``-format that's still useful for the operator. Returns
    a German string for the three patterns we know we can point the
    user at a concrete next step:

      * Image too large (5 MB limit) — ``v23.1.2``'s resize loop
        normally prevents this, but if it slips through (e.g. on a
        future plan that even 100 DPI JPEG can't compress under
        4.5 MB) the user gets the right action.
      * max_tokens / context length exceeded — split the plan.
      * Rate limit — wait a moment.

    The tests pin the exact mapping so a future refactor can't
    silently swap a known error onto the diagnostic fallback.
    """
    msg = str(exc).lower()
    if "image" in msg and (
        "exceed" in msg or "too large" in msg or "5 mb" in msg
        or "5242880" in msg or "maximum" in msg
    ):
        return (
            "Der Plan ist zu groß für die KI-Analyse. Bitte exportieren "
            "Sie das PDF mit niedrigerer Auflösung oder teilen Sie es "
            "in kleinere Bereiche auf."
        )
    if "max_tokens" in msg or "context length" in msg or "context window" in msg:
        return (
            "Der Plan enthält zu viele Räume für eine einzelne Analyse. "
            "Bitte das PDF in mehrere Teilbereiche aufteilen."
        )
    if "rate" in msg and "limit" in msg:
        return (
            "Zu viele Anfragen an die KI. Bitte einen Moment warten "
            "und es erneut versuchen."
        )
    return None


def _format_page_error(
    page_number: int, exc: Exception, max_chars: int = 200
) -> str:
    """Format a per-page Vision-call error for the user-facing list.

    Two-tier strategy:

    1. Known errors (image-too-large, token-overflow, rate-limit) get
       a friendly German message via ``_translate_anthropic_error``.
       The user sees actionable copy (*"Plan zu groß — niedrigere
       Auflösung exportieren"*) instead of the raw API JSON.

    2. Unknown errors fall back to the diagnostic
       ``ClassName — truncated_message`` format the v23.1.1 hotfix
       introduced. Still better than just the class name when an
       operator needs to debug from the user's screenshot.

    Logger.exception in the caller still gets the full ``str(e)``
    (untruncated) for Railway log reading; the truncation here
    protects only the in-page banner.
    """
    friendly = _translate_anthropic_error(exc)
    if friendly is not None:
        return f"Seite {page_number}: {friendly}"
    err_msg = str(exc)[:max_chars]
    if not err_msg:
        return f"Seite {page_number}: {type(exc).__name__}"
    return f"Seite {page_number}: {type(exc).__name__} — {err_msg}"


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

    # v24.4.6 — dedicated single-thread-executor für alle fitz-
    # Operationen in diesem analyze_plan-Aufruf. PyMuPDF-Doc-Objekte
    # sind nicht thread-safe; mit max_workers=1 ist garantiert, dass
    # jeder run_in_executor-Call dasselbe Doc auf demselben Thread
    # anfasst, egal wie viele Anthropic-await-Switches dazwischen
    # liegen. Das Doc wird einmal geöffnet, sowohl für den Pre-Render
    # (``_render_all_pages``) als auch für die Two-Pass-Clip-Renders
    # in ``_extract_rooms_two_pass`` benutzt, und am Ende des
    # äußeren ``finally``-Blocks geschlossen.
    fitz_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix=f"fitz-{plan_id}"
    )
    doc = None
    loop = asyncio.get_event_loop()

    try:
        # Step 1: Open PDF + render every page once.
        #
        # Failure-mode catalogue:
        #
        #   * ``FileNotFoundError`` — disk-state mismatch (Railway
        #     ephemeral storage, manual delete). Map to a clear
        #     "re-upload" message.
        #   * ``fitz.FileDataError`` — PDF body is structurally
        #     unparseable. Modern PyMuPDF subclasses this from
        #     RuntimeError, older from a base type. Map to "PDF
        #     nicht öffenbar".
        #   * ``RuntimeError`` — older PyMuPDF's open-time error
        #     class. Same handling as ``FileDataError``. We
        #     deliberately bound this to the open call only, so per-
        #     page render failures (which v23.1.3 wraps inside
        #     ``_render_all_pages`` itself) don't accidentally surface
        #     as "PDF nicht öffenbar" — that misclassification was
        #     the v23.1.2 regression this hotfix targets.
        #
        # Per-page render failures come back via the ``render_errors``
        # list (not as exceptions) so one unrenderable page doesn't
        # abort the whole upload.
        try:
            import fitz  # PyMuPDF
            doc = await loop.run_in_executor(
                fitz_executor, fitz.open, plan.file_path
            )
            rendered_pages, render_errors = await loop.run_in_executor(
                fitz_executor, _render_all_pages, doc
            )
        except FileNotFoundError:
            logger.exception(
                "pdf_open.file_missing plan=%s file=%s",
                plan_id, plan.file_path,
            )
            raise PlanAnalysisError(
                "Die hochgeladene PDF-Datei wurde auf dem Server nicht "
                "gefunden. Bitte laden Sie den Plan erneut hoch."
            )
        except RuntimeError as e:
            # PyMuPDF raises RuntimeError exclusively for open-time
            # corruption now that v23.1.3 wraps render-time failures
            # inside ``_render_all_pages``. Full stack to logs, friendly
            # German message to user.
            logger.exception(
                "pdf_open.failed plan=%s file=%s err=%s: %s",
                plan_id, plan.file_path, type(e).__name__, e,
            )
            raise PlanAnalysisError(
                "Die PDF-Datei konnte nicht gelesen werden. Bitte "
                "prüfen Sie, ob die Datei nicht beschädigt ist."
            )

        plan.page_count = len(rendered_pages)
        await db.flush()

        if not rendered_pages and not render_errors:
            # No pages and no errors → file was empty / had zero
            # pages. Distinct from "every page failed to render".
            raise PlanAnalysisError("Die PDF enthält keine Seiten.")

        if not rendered_pages:
            # All pages failed to render. Surface the first three
            # specific errors so the user sees what went wrong
            # rather than a generic "no rooms extracted".
            joined = "; ".join(render_errors[:3])
            if len(render_errors) > 3:
                joined += f" (und {len(render_errors) - 3} weitere)"
            raise PlanAnalysisError(
                "Keine Seite des PDFs konnte für die KI-Analyse "
                f"vorbereitet werden. {joined}"
            )

        if len(rendered_pages) > settings.max_plan_pages:
            raise PlanAnalysisError(
                f"Die PDF hat {len(rendered_pages)} Seiten — maximal "
                f"{settings.max_plan_pages} Seiten pro Plan erlaubt. Bitte "
                f"teilen Sie die Datei auf."
            )

        logger.info(
            "Analyzing %d pages for plan %s (render-errors=%d)",
            len(rendered_pages), plan_id, len(render_errors),
        )

        # Late import for the rate-limit exception type. We've already
        # confirmed the API key is set, so paying for the anthropic
        # import now is fine; doing it here (rather than at module
        # top) keeps cold imports cheap when plan analysis isn't
        # exercised.
        import anthropic

        # Step 2: Two-Pass-Vision-Extraction per Seite (v24.4.6).
        # Sequenziell — concurrent Claude-Calls bringen für typische
        # Plan-Größen wenig und multiplizieren Quota-Spikes.
        # We track ``(page_number, result)`` tuples so the persist
        # phase can stamp each room with the page it was extracted
        # from — Vision doesn't see the page index itself, the
        # pipeline owns that fact.
        #
        # ``_extract_rooms_two_pass`` macht intern:
        #   1. Low-Res-Render (1536 px) → Haiku-BBox-Probe.
        #   2. Fail-Safe falls BBox unbrauchbar → Sonnet auf den
        #      bereits hochauflösenden ``fallback_bytes`` (= Pre-Render
        #      aus Schritt 1 oben, der heutige Single-Pass).
        #   3. Sonst: High-Res-Clip, ggf. 2×2-Kacheln, Sonnet-Call(s).
        # Die Funktion liefert ein einzelnes Page-Result (oder None
        # bei unbehebbarem Fehler) — identische Form zum vorherigen
        # ``_extract_rooms_from_image``-Output, sodass Step 3 unverändert
        # bleibt.
        all_results: list[tuple[int, dict]] = []
        # Seed page_errors with any per-page render failures we
        # already collected. This way the user sees one consistent
        # list of "what went wrong on which page", regardless of
        # whether the failure was at render-time or Vision-time.
        page_errors: list[str] = list(render_errors)
        for page_number, fallback_bytes, fallback_mime in rendered_pages:
            try:
                result = await _extract_rooms_two_pass(
                    doc=doc,
                    page_number=page_number,
                    fitz_executor=fitz_executor,
                    fallback_image_bytes=fallback_bytes,
                    fallback_mime_type=fallback_mime,
                    plan_id=plan_id,
                )
                if result is not None:
                    all_results.append((page_number, result))
                else:
                    page_errors.append(
                        f"Seite {page_number}: KI-Antwort nicht verwertbar"
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "Claude Vision timeout on page %d of plan %s",
                    page_number, plan_id,
                )
                page_errors.append(
                    f"Seite {page_number}: Zeitüberschreitung bei der "
                    f"KI-Analyse"
                )
            except anthropic.RateLimitError:
                # Rate limits are account-level; hitting one on page N
                # means page N+1 will hit it too. Abort with a specific
                # message instead of letting every remaining page fail
                # through the generic handler.
                logger.warning(
                    "Anthropic rate limit on page %d of plan %s — aborting",
                    page_number,
                    plan_id,
                )
                raise PlanAnalysisError(
                    "Zu viele Anfragen an die KI. Bitte warten Sie einen "
                    "Moment und versuchen Sie es erneut."
                )
            except Exception as e:  # noqa: BLE001
                # ``str(e)`` carries the Anthropic message body
                # (e.g. "max_tokens insufficient for expected
                # output", "image dimension exceeds 7990 px") which
                # is the only useful diagnostic data when the type
                # name alone is too generic. Logged at full length;
                # surfaced to the user truncated via _format_page_error.
                logger.exception(
                    "Claude Vision call failed for page %d of plan %s: "
                    "%s — %s",
                    page_number,
                    plan_id,
                    type(e).__name__,
                    str(e)[:1000],
                )
                page_errors.append(_format_page_error(page_number, e))

        # Step 3: Persist
        total_rooms = 0
        for page_number, page_result in all_results:
            rooms_created = await _store_extraction_result(
                page_result, plan, db, page_number=page_number
            )
            total_rooms += rooms_created

        # v24.5 — Stufe 4a TROCKENLAUF (measure-only). Läuft NUR bei aktivem
        # Flag; Default "off" überspringt den Block komplett (das Modul wird
        # dann nicht einmal importiert -> Pipeline exakt wie zuvor). Schreibt
        # NICHTS, ändert das Vision-Ergebnis NICHT. Jeder Fehler hier wird
        # geschluckt, damit der Trockenlauf die Analyse niemals kippen kann.
        if settings.textlayer_backfill_mode != "off" and doc is not None:
            from app.plan_analysis import textlayer_backfill as _tb
            for _pn, _res in all_results:
                try:
                    _pdict = await loop.run_in_executor(
                        fitz_executor, lambda p=_pn: doc[p - 1].get_text("dict")
                    )
                    logger.info(_tb.format_log(_tb.measure_page(_pdict, _res, page=_pn), plan_id))
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "textlayer_backfill.measure_failed page=%d", _pn, exc_info=True
                    )

        # Decide the final status. If at least one page produced rooms,
        # call it "completed" (partial success is still useful); if
        # nothing came back at all, mark it failed so the user knows.
        if total_rooms == 0:
            plan.analysis_status = "failed"
            await db.flush()
            # v24.4.5 — KI-``notes`` einsammeln (das Feld setzt die KI
            # laut Prompt NUR im 0-Räume-Fall, z.B. "wirkt wie ein
            # Lageplan"). So bekommt der User einen konkreten Grund
            # statt der generischen Meldung. Dedupliziert + auf die
            # ersten 3 begrenzt, damit ein 20-seitiges PDF die Meldung
            # nicht sprengt.
            ki_notes: list[str] = []
            for _pn, r in all_results:
                if isinstance(r, dict) and r.get("notes"):
                    note = str(r["notes"]).strip()
                    if note and note not in ki_notes:
                        ki_notes.append(note)
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
                if ki_notes:
                    detail += " Hinweis der KI: " + " / ".join(ki_notes[:3])
            raise PlanAnalysisError(detail)

        plan.analysis_status = "completed"
        await db.flush()

        logger.info(
            "Plan analysis completed: plan_id=%s pages=%d rooms=%d errors=%d",
            plan_id,
            len(rendered_pages),
            total_rooms,
            len(page_errors),
        )

        return {
            "plan_id": str(plan_id),
            "pages_analyzed": len(rendered_pages),
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
    finally:
        # v24.4.6 — Doc + Executor sauber aufräumen, egal welchen
        # Pfad wir genommen haben (Erfolg, PlanAnalysisError, generic
        # Exception). Beides bewusst best-effort: ein Cleanup-Fehler
        # darf den eigentlichen Pfad nicht maskieren.
        if doc is not None:
            try:
                await loop.run_in_executor(fitz_executor, doc.close)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "two_pass.doc_close_failed plan=%s err=%s: %s",
                    plan_id, type(exc).__name__, exc,
                )
        fitz_executor.shutdown(wait=True)


# Anthropic's image-size limit is 5 MB on the binary payload (the
# decoded image, not the base64 string). We aim for 4.5 MB to keep a
# half-megabyte safety margin against off-by-some accounting on the
# server side.
_VISION_IMAGE_MAX_BYTES = 4_500_000

# Standard render DPI. 200 DPI is the v23.1.2 baseline (down from
# 300 in v22 and earlier) — Vision can still read room labels and
# dimension chains at 200 DPI on every plan we've tested, and it
# saves roughly 55 % of bytes vs 300 DPI rendering.
_VISION_DEFAULT_DPI = 200

# Floor on the resize ladder. Below this Vision starts losing room
# labels even on clean CAD output. If we hit this floor with JPEG
# and still can't fit, the plan is genuinely too large for a
# single-shot analysis and we surface a German user-message asking
# the operator to split or down-sample the PDF.
_VISION_MIN_DPI = 100

# DPI ladder for the resize loop. Each step is roughly 25 % smaller
# than the previous; values are tuned so the resulting pixel count
# halves predictably and so the 100 DPI floor is the last entry.
_VISION_DPI_LADDER = (200, 150, 112, 100)

# JPEG quality factor used as the size-reduction fallback. 85 % is
# the architectural-plan sweet-spot — high enough to keep thin lines
# (vermassung, room labels) crisp, low enough that file size drops
# ~70 % vs the equivalent PNG.
_VISION_JPEG_QUALITY = 85


# ---------------------------------------------------------------------------
# v24.4.6 — Two-Pass-Konstanten (Building-BBox-Probe + High-Res-Crop)
# ---------------------------------------------------------------------------
#
# Problem: bei Plänen mit Lageplan-Rand nimmt das Gebäude nur einen
# Bruchteil der Bildfläche ein. Anthropic resizet jedes Bild intern auf
# 1568 px Long-Edge → die Raum-Beschriftung ist nach dem Resize zu klein
# zum Lesen. Lösung: erst Haiku den Gebäude-Block lokalisieren lassen,
# dann den Bereich mit hoher DPI nachrendern, croppen, an Sonnet
# schicken. Details siehe ``_extract_rooms_two_pass``.

# Schritt 1: Vollbild bei bekannter fester Pixel-Größe rendern. 1536
# (statt 1568) verhindert ein verstecktes Anthropic-Internal-Resize an
# der API-Grenze — die Eingabe ist exakt was die KI nachher sieht.
_BBOX_PROBE_LONG_EDGE_PX = 1536

# Schritt 2: High-DPI fürs Crop-Rendering. 300 DPI ist CAD-Industrie-
# Standard für lesbare Architektur-Pläne; bei einem typischen
# Wohnungs-Crop ergibt das nach dem Resize-Schritt (siehe unten)
# deutlich mehr Detail als der 1536-px-Vollbild-Render.
_HIGH_RES_DPI = 300

# Schritt 3: Zielgröße für den finalen Räume-Call. Gleiche 1536-Grenze
# wie Schritt 1, gleicher Anthropic-Grund.
_VISION_LONG_EDGE_PX = 1536

# Fail-Safe-Schwellen für die BBox aus Schritt 1.
# > 95 % der Bildfläche → kein Lageplan rundherum, kein Crop nötig
# < 15 % der Bildfläche → fast sicher Fehl-Erkennung (KI hat etwas
#                         Kleines markiert statt das Gebäude)
_BBOX_TOO_LARGE_FRAC = 0.95
_BBOX_TOO_SMALL_FRAC = 0.15

# Tile-Schwelle in PDF-PUNKTEN (nicht Pixeln). Wenn der Crop physisch
# länger ist als ~81 cm (2300 pt = 800 mm), wird's nach Resize auf
# 1536 px Long-Edge eng mit der Label-Lesbarkeit — dann 2×2 kacheln.
# Bewusst konservativ: der 5 %-Kachel-Overlap kann ein Raum-Label auf
# der Kachel-Grenze trotzdem halbieren → Fläche fehlt. Lieber selten
# kacheln. Physische Einheit (PDF-Punkte) ist DPI-unabhängig — eine
# Verdopplung des High-Res-DPI ändert die Schwelle NICHT.
_TILE_THRESHOLD_LONG_EDGE_PT = 2300.0
_TILE_OVERLAP_FRAC = 0.05

# 5 % Rand um die BBox in Low-Res-Pixeln (vor der PDF-Punkt-Umrechnung),
# damit die Außenwände nicht hart abgeschnitten sind.
_BBOX_PADDING_FRAC = 0.05

# Schritt-1-Modell — Haiku reicht für reine BBox-Lokalisation (keine
# Lesen-Aufgabe). Das gute Modell bleibt für Schritt 3 (Räume).
_BBOX_MODEL = "claude-haiku-4-5"
_BBOX_MAX_TOKENS = 256


def _render_page_for_vision(
    page,
    *,
    page_number: int,
    max_bytes: int = _VISION_IMAGE_MAX_BYTES,
) -> tuple[bytes, str]:
    """Render one PDF page within Anthropic's 5 MB image limit.

    Strategy (v23.1.2 + v23.1.3 hardening):

      1. Each DPI step renders the page into an RGB pixmap (no
         alpha, no source-CMYK side effects), then tries PNG
         first, JPEG-quality-85 second.
      2. If both PNG and JPEG fit the threshold, the smaller wins —
         but PNG is preferred for the lossless quality on clean CAD
         output, so we ship PNG the moment it fits.
      3. ``RuntimeError`` from any individual ``tobytes`` call is
         logged but does not abort the render. We try the next
         DPI/format combination instead. PyMuPDF can fail
         ``tobytes("jpeg")`` for many reasons (RGBA-source despite
         our alpha=False request, exotic colorspaces, memory
         pressure on large pages); falling through to a smaller
         render usually succeeds.
      4. Bottom of the DPI ladder with no successful render → a
         ``PlanAnalysisError`` whose message tells the user how to
         recover (split the PDF or export at lower DPI).

    Why ``alpha=False, colorspace=fitz.csRGB``
    ------------------------------------------
    JPEG cannot encode RGBA. PyMuPDF's ``get_pixmap()`` defaults to
    ``alpha=False``, but PDFs with transparency layers (watermarks,
    transparent overlays in modern CAD output) sometimes leak alpha
    through anyway — the ``tobytes("jpeg")`` call then raises a
    naked ``RuntimeError`` that the broader ``analyze_plan`` handler
    used to misclassify as "PDF nicht öffenbar". The explicit
    colorspace + alpha kwargs are belt-and-suspenders against that.

    Returns ``(image_bytes, mime_type)``.
    """
    import fitz  # PyMuPDF

    # Track the first attempted render so the resize-event log line
    # can name what we *started* with vs what we ended up shipping.
    first_attempt: tuple[int, str, int] | None = None
    last_exception: Exception | None = None

    for dpi in _VISION_DPI_LADDER:
        mat = fitz.Matrix(dpi / 72, dpi / 72)

        # Pixmap acquisition is its own failure surface. If
        # ``get_pixmap`` raises (corrupt page object, memory
        # pressure, exotic colorspace), the error is per-DPI — we
        # log it and try the next ladder step rather than failing
        # the whole page.
        try:
            pix = page.get_pixmap(
                matrix=mat,
                alpha=False,
                colorspace=fitz.csRGB,
            )
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.exception(
                "pdf_to_images.pixmap_failed page=%d dpi=%d: %s",
                page_number, dpi, exc,
            )
            last_exception = exc
            continue

        # First try PNG (lossless, preferred for clean CAD output).
        png_bytes: bytes | None = None
        try:
            png_bytes = pix.tobytes("png")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "pdf_to_images.png_encode_failed page=%d dpi=%d: %s",
                page_number, dpi, exc,
            )
            last_exception = exc

        if png_bytes is not None:
            logger.info(
                "pdf_to_images.page_rendered page=%d format=png dpi=%d "
                "bytes=%d",
                page_number, dpi, len(png_bytes),
            )
            if first_attempt is None:
                first_attempt = (dpi, "png", len(png_bytes))
            if len(png_bytes) <= max_bytes:
                if first_attempt != (dpi, "png", len(png_bytes)):
                    logger.warning(
                        "pdf_to_images.page_resized page=%d from='%d "
                        "dpi/%s/%d bytes' to='%d dpi/png/%d bytes'",
                        page_number,
                        first_attempt[0], first_attempt[1],
                        first_attempt[2],
                        dpi, len(png_bytes),
                    )
                return png_bytes, "image/png"

        # PNG too big at this DPI (or its encode failed) — try JPEG.
        try:
            jpeg_bytes = pix.tobytes("jpeg", jpg_quality=_VISION_JPEG_QUALITY)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "pdf_to_images.jpeg_encode_failed page=%d dpi=%d: %s",
                page_number, dpi, exc,
            )
            last_exception = exc
            # No image at this DPI in either format — drop down.
            continue

        logger.info(
            "pdf_to_images.page_rendered page=%d format=jpeg dpi=%d "
            "bytes=%d",
            page_number, dpi, len(jpeg_bytes),
        )
        if first_attempt is None:
            first_attempt = (dpi, "jpeg", len(jpeg_bytes))
        if len(jpeg_bytes) <= max_bytes:
            logger.warning(
                "pdf_to_images.page_resized page=%d from='%d dpi/%s/%d "
                "bytes' to='%d dpi/jpeg/%d bytes'",
                page_number,
                first_attempt[0], first_attempt[1], first_attempt[2],
                dpi, len(jpeg_bytes),
            )
            return jpeg_bytes, "image/jpeg"

        # Both PNG and JPEG over the cap at this DPI — drop down.

    # Fell off the ladder. Two distinct sub-cases:
    if last_exception is not None and first_attempt is None:
        # Never produced a single byte of image. Render itself is
        # broken on this page (corrupt content stream, exotic
        # colorspace, memory). Surface the type of error explicitly
        # so the operator's UI message is honest.
        raise PlanAnalysisError(
            f"Seite {page_number} konnte nicht in ein Bild "
            f"konvertiert werden ("
            f"{type(last_exception).__name__}: "
            f"{str(last_exception)[:120]}). Bitte das PDF prüfen "
            f"oder neu exportieren."
        )

    # Renders succeeded but always above the size cap. Genuine
    # "Plan zu komplex" case.
    raise PlanAnalysisError(
        "Der Plan ist zu groß für die KI-Analyse. Bitte exportieren "
        "Sie das PDF mit niedrigerer Auflösung oder teilen Sie es in "
        "kleinere Bereiche auf (z.B. Geschoss für Geschoss)."
    )


def _render_all_pages(
    doc,
) -> tuple[list[tuple[int, bytes, str]], list[str]]:
    """Render every page of an OPEN ``fitz.Document`` to Vision-ready bytes.

    v24.4.6 — extrahiert aus ``_pdf_to_images`` damit ``analyze_plan``
    das PDF nur EINMAL öffnen muss. PyMuPDF-Doc-Objekte sind nicht
    thread-safe; ein einziges Doc + ein dedicated single-thread-executor
    schließt jedes Cross-Thread-Risiko aus, das zwei separate
    ``fitz.open()``-Calls auf dieselbe Datei hätten.

    Returns ``(rendered_pages, render_errors)``:

    * ``rendered_pages`` is ``[(page_number, image_bytes, mime_type), …]``
      with one entry per *successfully* rendered page. Page numbers
      are 1-based and may be non-contiguous if individual pages
      failed (e.g. ``[(1, b"...", "image/png"), (3, b"...", "image/jpeg")]``
      when page 2 was unrenderable).
    * ``render_errors`` is a list of user-facing German strings, one
      per failed page (``"Seite 2: Plan zu groß für KI-Analyse..."``).
      The caller folds these into the project-wide ``page_errors``
      list so the user sees a precise per-page rundown.

    Per-page render-errors do NOT propagate; we want one bad page to
    skip itself rather than abort the whole upload.

    PyMuPDF is CPU-bound and releases the GIL during rendering; the
    caller dispatches this function from a thread executor so the
    event loop stays responsive.
    """
    rendered: list[tuple[int, bytes, str]] = []
    errors: list[str] = []
    max_pages = settings.max_plan_pages
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        page_number = i + 1
        try:
            data, mime = _render_page_for_vision(
                page, page_number=page_number
            )
        except PlanAnalysisError as exc:
            # Per-page render failure with a user-facing message
            # already attached. Collect for the per-page error
            # list, do not abort the full upload.
            errors.append(f"Seite {page_number}: {exc.detail}")
            continue
        except Exception as exc:  # noqa: BLE001
            # Truly unexpected — should not happen, render
            # function already wraps known failure modes. Log
            # the full stack and surface as a per-page error so
            # the user sees something concrete.
            logger.exception(
                "pdf_to_images.unexpected_render_failure "
                "page=%d: %s",
                page_number, exc,
            )
            errors.append(
                f"Seite {page_number} konnte nicht gerendert werden "
                f"({type(exc).__name__})."
            )
            continue
        rendered.append((page_number, data, mime))
    logger.info(
        "pdf_to_images.completed rendered=%d failed=%d",
        len(rendered), len(errors),
    )
    return rendered, errors


def _pdf_to_images(
    file_path: str,
) -> tuple[list[tuple[int, bytes, str]], list[str]]:
    """Convenience wrapper: open, render, close. PRE-v24.4.6 was the
    only entry point; new code (``analyze_plan``) opens the doc itself
    and calls ``_render_all_pages`` directly so it can reuse the same
    handle for the Two-Pass-BBox-Probe. This wrapper stays for the
    Schnitt-pipeline and existing tests that pass file paths.

    Open-errors (corrupt PDF, missing file) propagate as their
    native exception type — the caller maps them to "PDF nicht
    öffenbar". Per-page render-errors do NOT propagate.
    """
    import fitz  # PyMuPDF

    # The open call is its own failure surface. We deliberately do
    # NOT swallow exceptions here — the caller's "PDF nicht öffenbar"
    # handler owns that path. If we caught and re-raised something
    # else, that handler couldn't tell open-error from render-error.
    doc = fitz.open(file_path)
    try:
        return _render_all_pages(doc)
    finally:
        doc.close()


async def _extract_rooms_from_image(
    image_bytes: bytes,
    page_number: int,
    *,
    mime_type: str = "image/png",
) -> dict | None:
    """Send one page image to Claude Vision and parse the JSON response.

    Returns the parsed dict on success, or ``None`` if the model's
    response could not be interpreted as JSON. Logs the raw response
    on parse failure so operators can see what came back.

    ``mime_type`` is supplied by the renderer (PNG for small pages,
    JPEG for large pages that needed the resize fallback) and is
    threaded through to Anthropic's ``source.media_type`` so the
    decoder picks the right codec.
    """
    # Late import — keeps module import cheap and avoids a hard dep
    # when plan analysis isn't being exercised.
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Wrap in wait_for so a stalled API call can't hang indefinitely.
    # Keep this model string in lock-step with ``lv_generator/generator.py``
    # and ``chat/assistant.py``. The previous pin
    # (``claude-sonnet-4-20250514``) is an older, date-suffixed ID that
    # Anthropic rotated out; requests against it came back 404/503 and
    # surfaced to the UI as a generic analysis failure. ``claude-sonnet-4-6``
    # is the current Sonnet generation.
    message = await asyncio.wait_for(
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=_VISION_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
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
        parsed = json.loads(json_str)
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

    # v24.4.5 — Diagnose-Logging. Token-Usage immer; bei 0 Räumen
    # zusätzlich eine WARNING mit der ROHEN Antwort + dem ``notes``-
    # Feld. Vorher war genau dieser Fall (valides JSON, aber
    # ``units == []``) ein blinder Fleck: das Ergebnis wurde still als
    # 0-Räume gewertet, ohne dass im Log stand, was die KI eigentlich
    # zurückgegeben hat. Mit dem Prompt-``notes``-Feld (siehe
    # room_extraction.txt, Sektion "WENN DU KEINE RÄUME FINDEST")
    # liefert die KI im Leer-Fall jetzt eine Begründung mit, die hier
    # geloggt und in die User-Fehlermeldung gehoben wird.
    usage = getattr(message, "usage", None)
    in_tok = getattr(usage, "input_tokens", None)
    out_tok = getattr(usage, "output_tokens", None)
    room_count = _count_extracted_rooms(parsed)
    logger.info(
        "vision.page_parsed page=%d rooms=%d input_tokens=%s output_tokens=%s",
        page_number,
        room_count,
        in_tok,
        out_tok,
    )
    if room_count == 0:
        notes = parsed.get("notes") if isinstance(parsed, dict) else None
        raw_preview = response_text[:2000].replace("\n", "\\n")
        logger.warning(
            "vision.zero_rooms page=%d notes=%r input_tokens=%s "
            "output_tokens=%s raw=%s",
            page_number,
            notes,
            in_tok,
            out_tok,
            raw_preview,
        )
    return parsed


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


def _count_extracted_rooms(parsed: object) -> int:
    """Count rooms across all units in a Vision extraction result.

    v24.4.5 — extracted as a pure helper so the 0-rooms diagnose-
    logging in ``_extract_rooms_from_image`` is unit-testable without
    mocking the Anthropic SDK. Deliberately tolerant of malformed
    shapes (non-dict input, missing/non-list ``units``, units without
    a ``rooms`` list) — a counting helper that feeds a log line must
    never raise, otherwise it would convert a "0 rooms" diagnostic
    into a hard pipeline failure.
    """
    if not isinstance(parsed, dict):
        return 0
    units = parsed.get("units")
    if not isinstance(units, list):
        return 0
    total = 0
    for unit in units:
        if not isinstance(unit, dict):
            continue
        rooms = unit.get("rooms")
        if isinstance(rooms, list):
            total += len(rooms)
    return total


# ---------------------------------------------------------------------------
# v24.4.6 — Two-Pass-Helpers (fitz rendering, BBox-Math, Pillow crop/tile)
# ---------------------------------------------------------------------------
#
# Alle fitz-anfassenden Helper sind SYNCHRON und müssen vom
# ``analyze_plan``-eigenen single-thread-executor aufgerufen werden
# (siehe Konstruktion dort). PyMuPDF-Doc-Objekte sind nicht thread-
# safe — ein dedizierter Executor ist die einzige Garantie, dass alle
# Doc-Operationen auf demselben Thread laufen, egal wie viele
# Anthropic-await-Switches dazwischen liegen.


def _render_page_long_edge_jpeg(
    doc, page_number: int, long_edge_px: int,
) -> tuple[bytes, int, int]:
    """Render the whole page so its longer pixel edge equals ``long_edge_px``.

    Used für die BBox-Probe in Schritt 1: das Bild geht 1:1 an Haiku,
    ohne weiteres Resize an der API-Grenze. Liefert
    ``(jpeg_bytes, width_px, height_px)``.

    Muss im fitz-Executor laufen (Doc-Access).
    """
    import fitz  # PyMuPDF

    page = doc[page_number - 1]
    page_w_pts = float(page.rect.width)
    page_h_pts = float(page.rect.height)
    long_edge_pts = max(page_w_pts, page_h_pts)
    # ``scale`` ist der Faktor pt→pixel. fitz.Matrix nimmt sx/sy
    # separat, beides identisch hier (uniform scaling).
    scale = long_edge_px / long_edge_pts
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
    jpeg_bytes = pix.tobytes("jpeg", jpg_quality=_VISION_JPEG_QUALITY)
    return jpeg_bytes, pix.width, pix.height


def _render_page_clip_jpeg(
    doc, page_number: int, clip_rect, dpi: int = _HIGH_RES_DPI,
) -> tuple[bytes, int, int]:
    """Render only ``clip_rect`` of the page at ``dpi``.

    Liefert ``(jpeg_bytes, width_px, height_px)``. clip_rect ist eine
    ``fitz.Rect`` in PDF-Punkten. PyMuPDF rendert nur den Clip; das
    Vollbild muss nicht zwischengelagert werden, was bei A0/A1-Plänen
    relevant Memory spart.

    Muss im fitz-Executor laufen.
    """
    import fitz  # PyMuPDF

    page = doc[page_number - 1]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(
        matrix=mat,
        clip=clip_rect,
        alpha=False,
        colorspace=fitz.csRGB,
    )
    jpeg_bytes = pix.tobytes("jpeg", jpg_quality=_VISION_JPEG_QUALITY)
    return jpeg_bytes, pix.width, pix.height


def _bbox_fail_safe_reason(
    bbox: dict | None, *, image_width: int, image_height: int,
) -> str | None:
    """Pure helper: should the Two-Pass-Pfad auf Single-Pass fallen?

    Liefert einen Reason-String (für Logging + Fallback-Entscheidung)
    oder ``None`` wenn die BBox brauchbar ist.

    Reasons:
      ``bbox_parse_failed``  — bbox is None (Haiku-JSON kaputt)
      ``bbox_invalid_shape`` — fehlende oder nicht-numerische Felder
      ``bbox_out_of_bounds`` — Koords negativ oder außerhalb [0, W/H]
      ``bbox_too_large``     — Fläche > _BBOX_TOO_LARGE_FRAC
      ``bbox_too_small``     — Fläche < _BBOX_TOO_SMALL_FRAC
    """
    if bbox is None:
        return "bbox_parse_failed"
    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        w = float(bbox["width"])
        h = float(bbox["height"])
    except (KeyError, TypeError, ValueError):
        return "bbox_invalid_shape"
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return "bbox_out_of_bounds"
    if x + w > image_width + 1 or y + h > image_height + 1:
        # +1 toleriert ein einzelnes Pixel Rundung am Rand.
        return "bbox_out_of_bounds"
    total_area = float(image_width) * float(image_height)
    if total_area <= 0:
        return "bbox_out_of_bounds"
    bbox_area = w * h
    frac = bbox_area / total_area
    if frac > _BBOX_TOO_LARGE_FRAC:
        return "bbox_too_large"
    if frac < _BBOX_TOO_SMALL_FRAC:
        return "bbox_too_small"
    return None


def _compute_clip_rect_tuple(
    bbox_px: dict, *,
    source_width_px: int, source_height_px: int,
    pdf_width_pts: float, pdf_height_pts: float,
    padding_frac: float = _BBOX_PADDING_FRAC,
) -> tuple[float, float, float, float]:
    """Pure-math backbone of ``_bbox_to_pdf_rect`` — KEIN fitz-Import.

    Skaliert eine Low-Res-Pixel-BBox + Padding-Margin auf
    PDF-Punkt-Koords ``(x0, y0, x1, y1)``, mit Clamping auf die
    Page-Grenzen. Skalierungsfaktor pixel→pt: ``pdf_width_pts /
    source_width_px`` (uniform in x und y, weil der Low-Res-Renderer
    uniform skaliert hat).

    Bewusst als Tuple-Rückgabe und ohne fitz-Abhängigkeit: macht die
    Skalierungs- und Padding-Mathe unit-testbar, ohne dass die
    pymupdf-DLL geladen werden muss (Windows-AppLocker-freundlich).
    Der fitz.Rect-Konstruktor sitzt im dünnen Wrapper darunter.
    """
    x_px = float(bbox_px["x"])
    y_px = float(bbox_px["y"])
    w_px = float(bbox_px["width"])
    h_px = float(bbox_px["height"])

    # Padding-Margin auf Pixel-Ebene, dann clampen.
    pad_x = w_px * padding_frac
    pad_y = h_px * padding_frac
    x0_px = max(0.0, x_px - pad_x)
    y0_px = max(0.0, y_px - pad_y)
    x1_px = min(float(source_width_px), x_px + w_px + pad_x)
    y1_px = min(float(source_height_px), y_px + h_px + pad_y)

    sx = pdf_width_pts / float(source_width_px)
    sy = pdf_height_pts / float(source_height_px)
    return (x0_px * sx, y0_px * sy, x1_px * sx, y1_px * sy)


def _bbox_to_pdf_rect(
    bbox_px: dict, *,
    source_width_px: int, source_height_px: int,
    pdf_width_pts: float, pdf_height_pts: float,
    padding_frac: float = _BBOX_PADDING_FRAC,
):
    """Thin wrapper: liefert eine ``fitz.Rect`` aus den
    ``_compute_clip_rect_tuple``-Math-Koords, für die Verwendung in
    fitz-APIs (``page.get_pixmap(clip=...)``).
    """
    import fitz  # PyMuPDF

    x0, y0, x1, y1 = _compute_clip_rect_tuple(
        bbox_px,
        source_width_px=source_width_px,
        source_height_px=source_height_px,
        pdf_width_pts=pdf_width_pts,
        pdf_height_pts=pdf_height_pts,
        padding_frac=padding_frac,
    )
    return fitz.Rect(x0, y0, x1, y1)


def _resize_long_edge_pillow(
    image_bytes: bytes, *,
    target_long_edge: int,
    jpeg_quality: int = _VISION_JPEG_QUALITY,
) -> tuple[bytes, int, int]:
    """Pillow-Resize wenn das Bild länger als ``target_long_edge`` ist.

    Liefert ``(jpeg_bytes, w, h)``. Wenn das Bild bereits kleiner ist,
    wird es 1:1 zurück-rejpegged (gleiche Qualität, kein Pixel-Verlust).
    """
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as img:
        if max(img.width, img.height) > target_long_edge:
            img.thumbnail(
                (target_long_edge, target_long_edge),
                resample=Image.Resampling.LANCZOS,
            )
        out = io.BytesIO()
        # Mode 'RGB' sicherstellen — Pillow speichert sonst RGBA-JPEG
        # nicht ab. Unsere fitz-Pixmaps sind RGB, aber defensive coding.
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        rgb.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
        return out.getvalue(), rgb.width, rgb.height


def _should_tile(
    *, clip_rect,
    threshold_pt: float = _TILE_THRESHOLD_LONG_EDGE_PT,
) -> bool:
    """Pure: True wenn die längere Kante der Clip-Region in PDF-Punkten
    > ``threshold_pt`` ist.

    PDF-Punkte sind die stabile physische Einheit (1 pt = 1/72 inch ≈
    0.353 mm). Im Gegensatz zur Pixel-Größe nach High-Res-Render ist
    die Schwelle damit unabhängig vom gewählten DPI — eine
    Verdopplung des DPI ändert die Kachel-Entscheidung NICHT.
    """
    return max(float(clip_rect.width), float(clip_rect.height)) > threshold_pt


def _tile_2x2_pillow(
    image_bytes: bytes, *,
    overlap_frac: float = _TILE_OVERLAP_FRAC,
    jpeg_quality: int = _VISION_JPEG_QUALITY,
) -> list[bytes]:
    """In 2×2 Kacheln zerlegen mit ``overlap_frac`` Überlappung.

    Reihenfolge im Output: ``[TL, TR, BL, BR]``. Überlappung sorgt
    dafür, dass ein Raum-Label, das genau auf einer inneren Kachel-
    Grenze sitzt, in mindestens einer Kachel ganz auftaucht. Bei
    5 %-Überlap reicht das nicht für jedes Label — die Schwelle in
    ``_should_tile`` ist deshalb bewusst konservativ.
    """
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as img:
        w, h = img.size
        mid_x = w // 2
        mid_y = h // 2
        ovx = int(w * overlap_frac)
        ovy = int(h * overlap_frac)
        boxes = [
            (0,             0,             mid_x + ovx,  mid_y + ovy),       # TL
            (mid_x - ovx,   0,             w,            mid_y + ovy),       # TR
            (0,             mid_y - ovy,   mid_x + ovx,  h),                 # BL
            (mid_x - ovx,   mid_y - ovy,   w,            h),                 # BR
        ]
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        tiles: list[bytes] = []
        for box in boxes:
            tile = rgb.crop(box)
            buf = io.BytesIO()
            tile.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            tiles.append(buf.getvalue())
        return tiles


def _normalise_room_key(name: object) -> str:
    """Dedupe-key for room names across tiles. Lowercased, whitespace
    collapsed, leading/trailing stripped. Non-strings collapse to ''."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.lower().split())


def _merge_tiled_results(results: list[dict | None]) -> dict:
    """Aus mehreren Kachel-Ergebnissen ein einziges Page-Result bauen.

    Dedupe nach normalisiertem Raumnamen über alle Kacheln/Units. Die
    erste Sichtung gewinnt — spätere Duplikate werden verworfen. Wenn
    keine Kachel auch nur einen Raum geliefert hat, kommt ein leeres
    Result mit ``notes`` zurück, damit der 0-Räume-Diagnose-Pfad in
    ``_extract_rooms_from_image`` auch hier eine sinnvolle Meldung
    bekommt.
    """
    seen_keys: set[str] = set()
    merged_units: list[dict] = []
    notes_collected: list[str] = []
    floor_name: str | None = None
    floor_level: int | None = None

    for r in results:
        if not isinstance(r, dict):
            continue
        if floor_name is None and isinstance(r.get("floor_name"), str):
            floor_name = r["floor_name"]
        if floor_level is None and isinstance(r.get("floor_level"), int):
            floor_level = r["floor_level"]
        if r.get("notes"):
            notes_collected.append(str(r["notes"]).strip())
        units = r.get("units") or []
        if not isinstance(units, list):
            continue
        for unit in units:
            if not isinstance(unit, dict):
                continue
            rooms = unit.get("rooms") or []
            if not isinstance(rooms, list):
                continue
            deduped_rooms = []
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                key = _normalise_room_key(room.get("room_name"))
                if key and key in seen_keys:
                    continue
                if key:
                    seen_keys.add(key)
                deduped_rooms.append(room)
            if deduped_rooms:
                merged_units.append({**unit, "rooms": deduped_rooms})

    merged: dict = {
        "floor_name": floor_name,
        "floor_level": floor_level,
        "units": merged_units,
    }
    # ``notes`` nur setzen wenn die Kacheln-Summe 0 Räume war — sonst
    # würde ein einzelnes Kachel-notes (z.B. "Plankopf-Kachel leer")
    # fälschlich als "Plan-Problem" in die User-Fehlermeldung landen.
    if not merged_units and notes_collected:
        merged["notes"] = " / ".join(dict.fromkeys(notes_collected))
    return merged


# ---------------------------------------------------------------------------
# Debug-Snapshots — Diagnostik während Beta
# ---------------------------------------------------------------------------


def _debug_crop_dir(plan_id: UUID) -> Path:
    """Pfad-Konstruktor für die Snapshot-Ablage; legt Ordner an."""
    base = Path(settings.upload_dir) / "debug-crops" / str(plan_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _save_debug_crop(
    payload: bytes, *,
    plan_id: UUID, page_number: int, stage: str, extension: str = "jpg",
) -> None:
    """Speichere ein Zwischenresultat unter
    ``{upload_dir}/debug-crops/{plan_id}/page-{N}-{stage}.{ext}``.

    No-op wenn ``settings.debug_save_crops`` False ist (default). Bei
    Disk-Fehler nur loggen, nie raisen — Diagnostik darf den
    Hauptpfad nicht brechen.
    """
    if not settings.debug_save_crops:
        return
    try:
        out_dir = _debug_crop_dir(plan_id)
        path = out_dir / f"page-{page_number}-{stage}.{extension}"
        path.write_bytes(payload)
        logger.info(
            "debug_crop.saved plan=%s page=%d stage=%s path=%s bytes=%d",
            plan_id, page_number, stage, path, len(payload),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "debug_crop.failed plan=%s page=%d stage=%s err=%s: %s",
            plan_id, page_number, stage, type(exc).__name__, exc,
        )


def _save_debug_json(
    payload: dict, *,
    plan_id: UUID, page_number: int, stage: str,
) -> None:
    """JSON-Variante von ``_save_debug_crop`` — für BBox-Koordinaten,
    Skalierungs-Faktoren, Fail-Safe-Reasons. Gleicher No-op + No-Raise
    Vertrag."""
    if not settings.debug_save_crops:
        return
    try:
        out_dir = _debug_crop_dir(plan_id)
        path = out_dir / f"page-{page_number}-{stage}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info(
            "debug_crop.saved plan=%s page=%d stage=%s path=%s",
            plan_id, page_number, stage, path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "debug_crop.failed plan=%s page=%d stage=%s err=%s: %s",
            plan_id, page_number, stage, type(exc).__name__, exc,
        )


# ---------------------------------------------------------------------------
# Schritt 1 — Haiku-Call zur BBox-Lokalisation
# ---------------------------------------------------------------------------


async def _find_building_bbox(
    image_bytes: bytes, *,
    image_width: int, image_height: int,
    page_number: int,
) -> dict | None:
    """Schritt 1 des Two-Pass-Flows.

    Schickt das Low-Res-Bild an Haiku mit dem Building-BBox-Prompt
    und parst die Antwort. Liefert ``{x, y, width, height}`` (alle int)
    bei Erfolg, sonst ``None``. Validierung der numerischen Range
    macht ``_bbox_fail_safe_reason``.
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt = BUILDING_BBOX_PROMPT_TEMPLATE.format(
        image_width=image_width, image_height=image_height,
    )
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    try:
        message = await asyncio.wait_for(
            client.messages.create(
                model=_BBOX_MODEL,
                max_tokens=_BBOX_MAX_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            ),
            timeout=_CLAUDE_CALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "two_pass.bbox_timeout page=%d", page_number,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "two_pass.bbox_call_failed page=%d err=%s: %s",
            page_number, type(exc).__name__, str(exc)[:500],
        )
        return None

    response_text = "".join(
        getattr(block, "text", "") for block in (message.content or [])
    ).strip()
    usage = getattr(message, "usage", None)
    in_tok = getattr(usage, "input_tokens", None)
    out_tok = getattr(usage, "output_tokens", None)

    if not response_text:
        logger.warning(
            "two_pass.bbox_empty_response page=%d input_tokens=%s output_tokens=%s",
            page_number, in_tok, out_tok,
        )
        return None

    try:
        bbox = json.loads(_extract_json_blob(response_text))
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "two_pass.bbox_parse_failed page=%d err=%s raw=%s",
            page_number, e, response_text[:500].replace("\n", "\\n"),
        )
        return None

    if not isinstance(bbox, dict):
        logger.warning(
            "two_pass.bbox_not_dict page=%d raw=%s",
            page_number, response_text[:500].replace("\n", "\\n"),
        )
        return None

    logger.info(
        "two_pass.bbox_call page=%d input_tokens=%s output_tokens=%s bbox=%r",
        page_number, in_tok, out_tok, bbox,
    )
    return bbox


# ---------------------------------------------------------------------------
# Orchestrator — Schritt 1 + 2 + 3 + Fail-Safe + Kacheln + Debug-Save
# ---------------------------------------------------------------------------


async def _extract_rooms_two_pass(
    *,
    doc,
    page_number: int,
    fitz_executor: concurrent.futures.Executor,
    fallback_image_bytes: bytes,
    fallback_mime_type: str,
    plan_id: UUID,
) -> dict | None:
    """v24.4.6 — der neue Hauptpfad pro Seite.

    Flow:
      1. Low-Res-Render (1536 px Long-Edge) im fitz-Executor.
      2. Haiku-Call → BBox des Gebäudes.
      3. Fail-Safe-Check → wenn problematisch: Fallback auf den
         existierenden Single-Pass (``_extract_rooms_from_image`` auf
         dem hoch aufgelösten ``fallback_image_bytes``).
      4. Sonst: High-Res-Clip rendern, je nach Größe entweder
         resizen oder 2×2 kacheln.
      5. Sonnet-Call(s) für Schritt 3 (Räume + Flächen ablesen).
      6. Bei Kacheln: Ergebnisse mergen + Duplikate entfernen.

    Liefert das geparste Vision-Resultat oder ``None`` bei
    unbehebbarem Fehler. Logging deckt jeden Branch ab; debug
    snapshots werden bei aktivem Flag immer mitgeschrieben.
    """
    loop = asyncio.get_event_loop()

    # ---- Schritt 1: Low-Res-Render -----------------------------------
    try:
        low_res_bytes, low_res_w, low_res_h = await loop.run_in_executor(
            fitz_executor,
            _render_page_long_edge_jpeg,
            doc, page_number, _BBOX_PROBE_LONG_EDGE_PX,
        )
    except Exception as exc:  # noqa: BLE001
        # Render-Fehler in Schritt 1 → zurück zum klassischen Pfad
        # mit dem bereits gerenderten Fallback-Bild.
        logger.warning(
            "two_pass.low_res_render_failed page=%d err=%s: %s — "
            "falling back to single-pass",
            page_number, type(exc).__name__, str(exc)[:300],
        )
        return await _extract_rooms_from_image(
            fallback_image_bytes,
            page_number=page_number,
            mime_type=fallback_mime_type,
        )
    logger.info(
        "two_pass.low_res_rendered page=%d w=%d h=%d bytes=%d",
        page_number, low_res_w, low_res_h, len(low_res_bytes),
    )
    _save_debug_crop(
        low_res_bytes, plan_id=plan_id, page_number=page_number, stage="low_res",
    )

    # ---- Schritt 1b: BBox-Probe (Haiku) ------------------------------
    bbox = await _find_building_bbox(
        low_res_bytes,
        image_width=low_res_w, image_height=low_res_h,
        page_number=page_number,
    )

    # ---- Fail-Safe-Check ---------------------------------------------
    reason = _bbox_fail_safe_reason(
        bbox, image_width=low_res_w, image_height=low_res_h,
    )
    _save_debug_json(
        {
            "bbox": bbox,
            "low_res_dims": [low_res_w, low_res_h],
            "fail_safe_reason": reason,
        },
        plan_id=plan_id, page_number=page_number, stage="bbox",
    )
    if reason is not None:
        logger.warning(
            "two_pass.fallback page=%d reason=%s bbox=%r — using fallback image",
            page_number, reason, bbox,
        )
        return await _extract_rooms_from_image(
            fallback_image_bytes,
            page_number=page_number,
            mime_type=fallback_mime_type,
        )

    # ---- Schritt 2: High-Res-Clip rendern ----------------------------
    # Hole Page-Dimensionen im fitz-Executor (Doc-Access).
    def _page_dims() -> tuple[float, float]:
        page = doc[page_number - 1]
        return float(page.rect.width), float(page.rect.height)

    pdf_w_pts, pdf_h_pts = await loop.run_in_executor(fitz_executor, _page_dims)
    clip_rect = _bbox_to_pdf_rect(
        bbox,
        source_width_px=low_res_w, source_height_px=low_res_h,
        pdf_width_pts=pdf_w_pts, pdf_height_pts=pdf_h_pts,
    )

    try:
        crop_bytes, crop_w, crop_h = await loop.run_in_executor(
            fitz_executor,
            _render_page_clip_jpeg,
            doc, page_number, clip_rect, _HIGH_RES_DPI,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "two_pass.clip_render_failed page=%d err=%s: %s — falling back",
            page_number, type(exc).__name__, str(exc)[:300],
        )
        return await _extract_rooms_from_image(
            fallback_image_bytes,
            page_number=page_number,
            mime_type=fallback_mime_type,
        )
    logger.info(
        "two_pass.crop_rendered page=%d pdf_rect=(%.1f,%.1f,%.1f,%.1f) "
        "pixels=%dx%d bytes=%d",
        page_number,
        clip_rect.x0, clip_rect.y0, clip_rect.x1, clip_rect.y1,
        crop_w, crop_h, len(crop_bytes),
    )
    _save_debug_crop(
        crop_bytes, plan_id=plan_id, page_number=page_number, stage="high_res_crop",
    )

    # ---- Kachel-Entscheidung (physische Größe in pt) -----------------
    do_tile = _should_tile(clip_rect=clip_rect)
    logger.info(
        "two_pass.tile_decision page=%d tile=%s crop_pt=(%.1fx%.1f) threshold_pt=%.1f",
        page_number, do_tile,
        clip_rect.width, clip_rect.height, _TILE_THRESHOLD_LONG_EDGE_PT,
    )

    if do_tile:
        # 2×2 Kacheln (Pillow, läuft im default-Pool — kein fitz nötig).
        tiles = await loop.run_in_executor(
            None, _tile_2x2_pillow, crop_bytes,
        )
        tile_results: list[dict | None] = []
        for i, tile_bytes in enumerate(tiles):
            _save_debug_crop(
                tile_bytes, plan_id=plan_id, page_number=page_number,
                stage=f"tile_{i}",
            )
            # Jede Kachel separat auf 1536-Long-Edge bringen — die
            # geviertelten Kacheln sind oft noch deutlich > 1536.
            resized, _, _ = await loop.run_in_executor(
                None, _tile_resize_for_vision, tile_bytes,
            )
            result = await _extract_rooms_from_image(
                resized, page_number=page_number, mime_type="image/jpeg",
            )
            tile_results.append(result)
        merged = _merge_tiled_results(tile_results)
        merged_rooms = _count_extracted_rooms(merged)
        # Dedup-Statistik fürs Log: rohe Raum-Anzahl pro Kachel summiert
        # minus gemerged. Hilft beim Abschätzen, ob die Überlappung
        # systematisch Doppel-Erkennungen erzeugt.
        raw_room_sum = sum(
            _count_extracted_rooms(r) for r in tile_results if r is not None
        )
        logger.info(
            "two_pass.merge_dedup page=%d tiles=%d raw_rooms=%d merged_rooms=%d "
            "duplicates_removed=%d",
            page_number, len(tile_results), raw_room_sum, merged_rooms,
            raw_room_sum - merged_rooms,
        )
        return merged

    # ---- Einzelbild-Pfad: ggf. resizen + Sonnet-Call -----------------
    resized_bytes, resized_w, resized_h = await loop.run_in_executor(
        None, _tile_resize_for_vision, crop_bytes,
    )
    if (resized_w, resized_h) != (crop_w, crop_h):
        _save_debug_crop(
            resized_bytes, plan_id=plan_id, page_number=page_number,
            stage="resized",
        )
        logger.info(
            "two_pass.crop_resized page=%d from=%dx%d to=%dx%d bytes=%d",
            page_number, crop_w, crop_h, resized_w, resized_h, len(resized_bytes),
        )
    return await _extract_rooms_from_image(
        resized_bytes, page_number=page_number, mime_type="image/jpeg",
    )


def _tile_resize_for_vision(image_bytes: bytes) -> tuple[bytes, int, int]:
    """Convenience-Wrapper für die Default-Resize-Parameter im
    Two-Pass-Pfad: auf ``_VISION_LONG_EDGE_PX`` (1536) bringen."""
    return _resize_long_edge_pillow(
        image_bytes, target_long_edge=_VISION_LONG_EDGE_PX,
    )


async def _store_extraction_result(
    result: dict,
    plan: Plan,
    db: AsyncSession,
    *,
    page_number: int = 1,
) -> int:
    """Persist one page's Claude Vision result. Returns rooms created.

    ``page_number`` is the 1-based PDF page index this result came
    from. We inject it onto every Room so the Phase 2 pin viewer can
    pick the correct background image — Vision never claims its own
    page number; the pipeline owns that fact.
    """
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
            # Normalise the ceiling-height marker so the DB only ever
            # holds one of the four accepted values. Anything else
            # (empty, typo, hallucinated category) collapses to
            # "default" which the frontend flags amber.
            ceiling_source_raw = room_data.get("ceiling_height_source")
            ceiling_source = (
                ceiling_source_raw
                if ceiling_source_raw in _CEILING_SOURCE_VALUES
                else "default"
            )
            # If the model returned no height, force ``default`` even
            # if it also claimed "grundriss" — a missing value can't
            # have come from any specific plan region.
            height_m = room_data.get("height_m")
            if height_m in (None, 0, 0.0):
                ceiling_source = "default"
                # v24.6 — Geschoss-Höhe fan-out: hat das Stockwerk
                # eine Raumhöhen-Vorgabe (``floor_height_m``), erbt
                # der Raum sie statt des 2,50-Defaults. Source
                # ``floor`` hält die Herkunft sichtbar, damit spätere
                # Änderungen der Geschoss-Höhe diese Räume wieder-
                # finden. Echte Vision-Messwerte (der Zweig oben, wo
                # height_m gesetzt ist) bleiben unberührt.
                floor_height = (
                    float(floor.floor_height_m)
                    if floor.floor_height_m is not None
                    else None
                )
                if floor_height is not None and floor_height > 0:
                    height_m = floor_height
                    ceiling_source = "floor"

            # Vision either returned a perimeter (good case),
            # nothing-but-an-area (we estimate), or nothing at all
            # (real unknown — leave null and let the UI flag it).
            # The third argument carries Vision's own claim about
            # how it found the perimeter (``labeled`` / ``computed``,
            # or anything else which collapses to ``vision``). See
            # ``_resolve_perimeter`` docstring.
            persisted_perimeter, perimeter_source = _resolve_perimeter(
                room_data.get("perimeter_m"),
                room_data.get("area_m2"),
                room_data.get("perimeter_source"),
            )

            # Pin coordinates (v23.1). All four are validated as
            # strictly-positive integers via ``_coerce_positive_int``;
            # anything else collapses to None. Vision is allowed to
            # supply any subset (including zero) — Phase 2 frontend
            # renders pins only for rooms that have all four. We
            # always inject the pipeline's own ``page_number`` so it
            # stays trustworthy even if Vision skipped or hallucinated
            # the field.
            position_x = _coerce_positive_int(room_data.get("position_x"))
            position_y = _coerce_positive_int(room_data.get("position_y"))
            bbox_width = _coerce_positive_int(room_data.get("bbox_width"))
            bbox_height = _coerce_positive_int(room_data.get("bbox_height"))

            room = Room(
                unit_id=unit.id,
                plan_id=plan.id,
                name=room_data.get("room_name") or "Raum",
                room_number=room_data.get("room_number"),
                room_type=room_data.get("room_type"),
                area_m2=room_data.get("area_m2"),
                perimeter_m=persisted_perimeter,
                perimeter_source=perimeter_source,
                height_m=height_m,
                ceiling_height_source=ceiling_source,
                # v24.4 — Vision liefert idealerweise schon einen
                # Slug aus der erweiterten Prompt-Liste, schickt aber
                # gelegentlich Großschreibungen oder Free-Text. Der
                # Normalizer fängt beides auf einen kanonischen Slug
                # (oder lässt unbekannte Free-Texte stehen).
                floor_type=normalise_floor_covering(
                    room_data.get("floor_type")
                ),
                is_wet_room=bool(room_data.get("is_wet_room", False)),
                has_dachschraege=bool(room_data.get("has_dachschraege", False)),
                is_staircase=bool(room_data.get("is_staircase", False)),
                source="ai",
                ai_confidence=room_data.get("confidence", 0.0),
                position_x=position_x,
                position_y=position_y,
                page_number=page_number,
                bbox_width=bbox_width,
                bbox_height=bbox_height,
            )
            db.add(room)
            await db.flush()

            opening_inputs: list[OpeningInput] = []
            for opening_data in room_data.get("openings", []) or []:
                width = opening_data.get("width_m") or 1.0
                height = opening_data.get("height_m") or 1.0
                count = opening_data.get("count") or 1
                opening = Opening(
                    room_id=room.id,
                    opening_type=opening_data.get("opening_type") or "fenster",
                    width_m=width,
                    height_m=height,
                    count=count,
                    source="ai",
                )
                db.add(opening)
                opening_inputs.append(
                    OpeningInput(
                        width_m=float(width),
                        height_m=float(height),
                        count=int(count),
                    )
                )

            # Eagerly compute wall area so the rooms table has
            # numbers to show on first render — otherwise the
            # frontend would display "—" until the user clicks
            # "Wandflächen berechnen". We feed the calculator the
            # *resolved* perimeter (Vision-extracted OR estimated
            # from area) so estimated rooms also start with a
            # plausible non-zero gross/net rather than 0,00 m².
            # Genuinely-unknown rooms (no perimeter, no area) still
            # land at gross 0 — that's the "please enter" signal.
            calc = calculate_wall_areas(
                perimeter_m=persisted_perimeter,
                height_m=height_m,
                is_staircase=bool(room_data.get("is_staircase", False)),
                deductions_enabled=True,
                openings=opening_inputs,
                ceiling_height_source=ceiling_source,
            )
            room.wall_area_gross_m2 = calc.wall_area_gross_m2
            room.wall_area_net_m2 = calc.wall_area_net_m2
            room.applied_factor = calc.applied_factor
            # calculate_wall_areas may downgrade the source to
            # "default" if the height fell back — keep the DB in sync.
            room.ceiling_height_source = calc.ceiling_height_source
            # v24.3.1 — default-height writeback. Pre-v24.3.1 the
            # pipeline persisted ``height_m=None`` whenever Vision
            # didn't extract a height (typical on Grundriss-only
            # uploads — heights live in Schnitt-Pläne). The wall-
            # calc table then displayed a fake "2,50 (Standard)"
            # via the frontend's display-override, and the
            # Mengenermittlungs-PDF rendered "—" because it reads
            # the honest DB value. By writing the 2,50 fallback
            # back here we keep the DB internally consistent: a
            # row whose wall-calc cache claims 2,50 also has
            # ``height_m=2.50`` on disk. ``ceiling_height_source``
            # stays ``"default"`` so the UI's Standard-Pille
            # remains accurate and the user is still prompted to
            # confirm — the value is just a non-null placeholder.
            # Mirrors the equivalent writeback in
            # ``_recalculate_walls_and_persist`` (rooms.py).
            if room.height_m is None:
                room.height_m = calc.height_used_m

            rooms_created += 1

    await db.flush()
    return rooms_created


# ---------------------------------------------------------------------------
# v24.2 — Schnitt-Plan Höhen-Extraktion (Feature 4)
# ---------------------------------------------------------------------------


SCHNITT_HEIGHT_PROMPT = (
    Path(__file__).parent / "prompts" / "schnitt_height_extraction.txt"
).read_text(encoding="utf-8")


# Plausibility bounds for extracted heights. Anything outside this
# range is dropped before persisting — the model sometimes gloms
# onto window-sill heights ("0,90 m") or building-total heights
# ("12,40 m") and labels them as Raumhöhe. Bounds chosen to fit
# every plausible Austrian residential / light-commercial room.
_SCHNITT_MIN_HEIGHT_M = 1.5
_SCHNITT_MAX_HEIGHT_M = 5.0


async def analyze_schnitt_plan(plan_id: UUID, db: AsyncSession) -> dict:
    """v24.2 — extract ``height_m`` from a Schnitt-Plan and write it
    onto matching grundriss-rooms.

    Pipeline shape mirrors ``analyze_plan`` but:

      * Prompts Vision with ``SCHNITT_HEIGHT_PROMPT`` (room name +
        floor + height only — no full room geometry).
      * Does NOT create new Building/Floor/Unit/Room rows. Schnitt
        plans are referenced data, not source-of-truth for the
        building tree.
      * Matches extracted ``raumname`` strings against existing
        rooms in the same project via ``_match_schnitt_height``.
        On match: ``room.height_m`` is set, ``ceiling_height_source``
        flips to ``"schnitt"``.

    Returns a dict with the standard ``plan_analyzed`` shape plus
    Schnitt-specific counts (``heights_extracted``, ``heights_matched``,
    ``rooms_in_project``) so the frontend toast can give a precise
    "X of Y rooms updated" message and the analytics event has
    something to log.

    Failure modes are surfaced via ``PlanAnalysisError`` exactly
    like ``analyze_plan`` does so the route handler's existing
    ``except PlanAnalysisError`` keeps working.
    """
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise PlanAnalysisError("Plan wurde nicht gefunden.")

    if not settings.anthropic_api_key:
        logger.error(
            "Schnitt analysis requested but ANTHROPIC_API_KEY is not set"
        )
        raise PlanAnalysisError(
            "KI-Analyse ist derzeit nicht verfügbar — der Claude-API-Schlüssel "
            "ist nicht konfiguriert. Bitte kontaktieren Sie den Support."
        )

    plan.analysis_status = "processing"
    await db.flush()

    logger.info(
        "Starting Schnitt analysis: plan_id=%s file=%s",
        plan_id,
        plan.file_path,
    )

    try:
        # Re-use the existing PDF→images render path verbatim.
        # Same DPI ladder, same JPEG fallback, same page cap.
        try:
            rendered_pages, render_errors = await asyncio.to_thread(
                _pdf_to_images, plan.file_path
            )
        except FileNotFoundError:
            raise PlanAnalysisError(
                "Die hochgeladene PDF-Datei wurde auf dem Server nicht "
                "gefunden. Bitte laden Sie den Plan erneut hoch."
            )
        except RuntimeError as e:
            logger.exception(
                "schnitt_pdf_open.failed plan=%s err=%s: %s",
                plan_id,
                type(e).__name__,
                e,
            )
            raise PlanAnalysisError(
                "Die PDF-Datei konnte nicht gelesen werden. Bitte "
                "prüfen Sie, ob die Datei nicht beschädigt ist."
            )

        plan.page_count = len(rendered_pages)
        await db.flush()

        if not rendered_pages and not render_errors:
            raise PlanAnalysisError("Die PDF enthält keine Seiten.")
        if not rendered_pages:
            joined = "; ".join(render_errors[:3])
            raise PlanAnalysisError(
                "Keine Seite des Schnitt-Plans konnte für die KI-Analyse "
                f"vorbereitet werden. {joined}"
            )
        if len(rendered_pages) > settings.max_plan_pages:
            raise PlanAnalysisError(
                f"Die PDF hat {len(rendered_pages)} Seiten — maximal "
                f"{settings.max_plan_pages} Seiten pro Plan erlaubt."
            )

        import anthropic  # late import — same pattern as analyze_plan

        # Vision call per page. Schnitt-Pläne sind typischerweise
        # 1-2 Seiten; wenn jemand einen 20-Seiten-Schnitt hochlädt
        # akzeptieren wir das aber lassen den existing page-cap
        # greifen.
        all_extracted: list[dict] = []
        page_errors: list[str] = list(render_errors)
        for page_number, image_bytes, mime_type in rendered_pages:
            try:
                result = await _vision_call(
                    image_bytes,
                    mime_type=mime_type,
                    prompt_text=SCHNITT_HEIGHT_PROMPT,
                    page_number=page_number,
                )
            except asyncio.TimeoutError:
                page_errors.append(
                    f"Seite {page_number}: Zeitüberschreitung bei der "
                    f"KI-Analyse"
                )
                continue
            except anthropic.RateLimitError:
                page_errors.append(
                    f"Seite {page_number}: KI-Anfrage abgelehnt — Rate-Limit "
                    f"erreicht. Bitte einen Moment warten."
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Schnitt vision call failed plan=%s page=%d: %s",
                    plan_id, page_number, exc,
                )
                page_errors.append(
                    _format_page_error(page_number, exc)
                )
                continue

            if not isinstance(result, dict):
                page_errors.append(
                    f"Seite {page_number}: KI-Antwort nicht verwertbar"
                )
                continue
            for entry in result.get("rooms", []) or []:
                if isinstance(entry, dict):
                    all_extracted.append(entry)

        # Sanitise + match. Empty extraction list is a soft-fail:
        # we still complete the run with a "no heights extracted"
        # report so the frontend can show a useful toast instead
        # of a generic 500.
        sanitized = _sanitize_schnitt_heights(all_extracted)
        matched_count = await _apply_schnitt_heights_to_rooms(
            sanitized, plan.project_id, db
        )

        # Total room count for the project — for the "X of Y" toast.
        rooms_in_project = await _count_project_rooms(plan.project_id, db)

        plan.analysis_status = "completed"
        await db.flush()

        logger.info(
            "Schnitt analysis completed: plan_id=%s pages=%d "
            "extracted=%d matched=%d project_rooms=%d errors=%d",
            plan_id,
            len(rendered_pages),
            len(sanitized),
            matched_count,
            rooms_in_project,
            len(page_errors),
        )

        return {
            "plan_id": str(plan_id),
            "pages_analyzed": len(rendered_pages),
            # Same key as analyze_plan returns so the analytics
            # event-shape stays uniform across both paths.
            "rooms_extracted": len(sanitized),
            # Schnitt-specific keys for the frontend toast.
            "heights_extracted": len(sanitized),
            "heights_matched": matched_count,
            "rooms_in_project": rooms_in_project,
            "page_errors": page_errors,
        }

    except PlanAnalysisError:
        plan.analysis_status = "failed"
        await db.flush()
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "Unexpected failure during Schnitt analysis %s: %s", plan_id, e
        )
        plan.analysis_status = "failed"
        await db.flush()
        raise PlanAnalysisError(
            "Bei der Höhen-Extraktion aus dem Schnitt-Plan ist ein "
            "unerwarteter Fehler aufgetreten. Bitte versuchen Sie es "
            "erneut oder kontaktieren Sie den Support."
        )


async def _vision_call(
    image_bytes: bytes,
    *,
    mime_type: str,
    prompt_text: str,
    page_number: int,
) -> dict | None:
    """Generic single-page Vision call. Returns parsed-JSON or None.

    Factored out so the Grundriss path
    (``_extract_rooms_from_image``) and the Schnitt path
    (``analyze_schnitt_plan``) share the same JSON-parse +
    timeout + error-shape contract without one drifting from the
    other.
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    message = await asyncio.wait_for(
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=_VISION_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
        ),
        timeout=_CLAUDE_CALL_TIMEOUT_S,
    )
    response_text = "".join(
        getattr(block, "text", "") for block in (message.content or [])
    ).strip()
    if not response_text:
        logger.warning(
            "Vision call empty content on page %d", page_number
        )
        return None
    try:
        return json.loads(_extract_json_blob(response_text))
    except (json.JSONDecodeError, ValueError) as e:
        preview = response_text[:500].replace("\n", "\\n")
        logger.warning(
            "Vision response not parseable as JSON on page %d: %s — preview=%s",
            page_number,
            e,
            preview,
        )
        return None


def _normalise_room_name(name: str | None) -> str:
    """Collapse a room name to its match-key form.

    Lower-case, whitespace-collapsed, dashes/dots/slashes removed,
    German vowel umlauts NOT folded — Vision returns "Küche" with
    the umlaut intact and so do we; folding would lose info on
    rooms like "Küche / Esszimmer". Whitespace normalisation gets
    us through the typical "WOHNEN / KOCHEN" vs "Wohnen/Kochen"
    casing/spacing variants.
    """
    if not name:
        return ""
    s = str(name).lower()
    # Drop common separators and strip — but keep umlauts so
    # "wohnzimmer" doesn't collapse onto "wohnzmmr".
    for ch in ("/", "-", ".", "_"):
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _normalise_floor_label(label: str | None) -> str:
    """Compact floor-label match-key. EG / 1.OG / KG / DG variants
    all fold into a stable lowercase form ("eg", "1og", "kg", "dg")
    so Vision's typography quirks don't block matches."""
    if not label:
        return ""
    s = str(label).lower().replace(".", "").replace(" ", "")
    return s


def _sanitize_schnitt_heights(
    raw: list[dict],
) -> list[dict]:
    """Drop entries with missing/implausible heights or names.

    Returns a list of sanitised dicts with only the three fields
    we care about (``raumname``, ``geschoss``, ``hoehe_m``) so the
    matching layer doesn't have to wade through Vision's optional
    extras.
    """
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("raumname")
        height = entry.get("hoehe_m") or entry.get("höhe_m")
        if not name:
            continue
        try:
            height_f = float(height)
        except (TypeError, ValueError):
            continue
        if not (_SCHNITT_MIN_HEIGHT_M <= height_f <= _SCHNITT_MAX_HEIGHT_M):
            continue
        out.append(
            {
                "raumname": str(name),
                "geschoss": entry.get("geschoss"),
                "hoehe_m": height_f,
            }
        )
    return out


async def _apply_schnitt_heights_to_rooms(
    extracted: list[dict],
    project_id: UUID,
    db: AsyncSession,
) -> int:
    """Match each extracted Schnitt-room against project rooms and
    write the height back. Returns the count of rooms successfully
    updated.

    Match strategy (in priority order):

      1. Name + floor exact match (after normalisation).
      2. Name-only exact match (after normalisation), iff the room
         is unambiguous in the project (only one room with that
         name across all floors).
      3. No match → entry skipped.

    Conservative on purpose: we'd rather miss a few heights than
    paint the wrong height onto the wrong room. The user sees a
    "X of Y rooms updated" toast and can fill the rest manually
    via the inline-edit cell.
    """
    if not extracted:
        return 0

    # Eager-load every room in the project once. The project's
    # building → floor → unit → room tree is small (typically
    # < 50 rooms) so a single round-trip + Python-side matching
    # is cheaper than per-extraction-entry queries.
    stmt = (
        select(Room, Floor)
        .join(Unit, Room.unit_id == Unit.id)
        .join(Floor, Unit.floor_id == Floor.id)
        .join(Building, Floor.building_id == Building.id)
        .where(Building.project_id == project_id)
    )
    rows = (await db.execute(stmt)).all()

    # Build two indexes: (name, floor) → list[Room] and name → list[Room].
    by_name_floor: dict[tuple[str, str], list[Room]] = {}
    by_name: dict[str, list[Room]] = {}
    for room, floor in rows:
        nname = _normalise_room_name(room.name)
        nfloor = _normalise_floor_label(floor.name) if floor else ""
        by_name_floor.setdefault((nname, nfloor), []).append(room)
        by_name.setdefault(nname, []).append(room)

    matched = 0
    for entry in extracted:
        target_name = _normalise_room_name(entry["raumname"])
        target_floor = _normalise_floor_label(entry.get("geschoss"))
        height = entry["hoehe_m"]

        candidate: Room | None = None

        # Tier 1: name + floor match.
        nf_matches = by_name_floor.get((target_name, target_floor), [])
        if len(nf_matches) == 1:
            candidate = nf_matches[0]
        elif len(nf_matches) > 1:
            # Multiple rooms with the same name on the same floor —
            # ambiguous. Skip rather than pick the wrong one.
            logger.info(
                "schnitt.match_ambiguous name=%s floor=%s candidates=%d",
                target_name, target_floor, len(nf_matches),
            )
            continue

        # Tier 2: name-only match.
        if candidate is None:
            n_matches = by_name.get(target_name, [])
            if len(n_matches) == 1:
                candidate = n_matches[0]

        if candidate is None:
            continue

        # Apply. Source flips to ``schnitt`` so the WallCalc table
        # shows the correct provenance badge.
        candidate.height_m = height
        candidate.ceiling_height_source = "schnitt"
        matched += 1

    if matched:
        await db.flush()
    return matched


async def _count_project_rooms(
    project_id: UUID, db: AsyncSession
) -> int:
    """Total number of rooms in the project. Used by the Schnitt-
    analyze route's "X of Y" reporting."""
    from sqlalchemy import func

    stmt = (
        select(func.count(Room.id))
        .join(Unit, Room.unit_id == Unit.id)
        .join(Floor, Unit.floor_id == Floor.id)
        .join(Building, Floor.building_id == Building.id)
        .where(Building.project_id == project_id)
    )
    return int((await db.execute(stmt)).scalar_one() or 0)
