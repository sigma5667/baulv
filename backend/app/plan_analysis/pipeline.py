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
from app.services.wall_calculator import (
    OpeningInput,
    calculate_wall_areas,
    estimate_perimeter_from_area,
)


# Accepted values for the ceiling_height_source column. Any other
# string the model hands back collapses to "default" — the frontend's
# amber warning treats that as "user please confirm".
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

    try:
        # Step 1: Convert PDF pages to images.
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
        #     ``_pdf_to_images`` itself) don't accidentally surface
        #     as "PDF nicht öffenbar" — that misclassification was
        #     the v23.1.2 regression this hotfix targets.
        #
        # Per-page render failures come back via the ``render_errors``
        # list (not as exceptions) so one unrenderable page doesn't
        # abort the whole upload.
        try:
            rendered_pages, render_errors = await asyncio.to_thread(
                _pdf_to_images, plan.file_path
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
            # inside ``_pdf_to_images``. Full stack to logs, friendly
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

        # Step 2: Claude Vision extraction per page. Runs sequentially
        # because concurrent Claude Vision calls don't buy much for
        # typical plan sizes and would multiply quota spikes.
        # We track ``(page_number, result)`` tuples so the persist
        # phase can stamp each room with the page it was extracted
        # from — Vision doesn't see the page index itself, the
        # pipeline owns that fact.
        all_results: list[tuple[int, dict]] = []
        # Seed page_errors with any per-page render failures we
        # already collected. This way the user sees one consistent
        # list of "what went wrong on which page", regardless of
        # whether the failure was at render-time or Vision-time.
        page_errors: list[str] = list(render_errors)
        for page_number, image_bytes, mime_type in rendered_pages:
            try:
                result = await _extract_rooms_from_image(
                    image_bytes, page_number=page_number, mime_type=mime_type
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


def _pdf_to_images(
    file_path: str,
) -> tuple[list[tuple[int, bytes, str]], list[str]]:
    """Convert PDF to per-page image bytes for Vision.

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

    Open-errors (corrupt PDF, missing file) propagate as their
    native exception type — the caller maps them to "PDF nicht
    öffenbar". Per-page render-errors do NOT propagate; we want one
    bad page to skip itself rather than abort the whole upload.

    PyMuPDF is CPU-bound and releases the GIL during rendering; we
    dispatch it from ``asyncio.to_thread`` in the caller so the
    event loop stays responsive.
    """
    import fitz  # PyMuPDF

    # The open call is its own failure surface. We deliberately do
    # NOT swallow exceptions here — the caller's "PDF nicht öffenbar"
    # handler owns that path. If we caught and re-raised something
    # else, that handler couldn't tell open-error from render-error.
    doc = fitz.open(file_path)
    try:
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
                    "page=%d file=%s: %s",
                    page_number, file_path, exc,
                )
                errors.append(
                    f"Seite {page_number} konnte nicht gerendert werden "
                    f"({type(exc).__name__})."
                )
                continue
            rendered.append((page_number, data, mime))
        logger.info(
            "pdf_to_images.completed file=%s rendered=%d failed=%d",
            file_path, len(rendered), len(errors),
        )
        return rendered, errors
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
            if room_data.get("height_m") in (None, 0, 0.0):
                ceiling_source = "default"

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
                height_m=room_data.get("height_m"),
                ceiling_height_source=ceiling_source,
                floor_type=room_data.get("floor_type"),
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
                height_m=room_data.get("height_m"),
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

            rooms_created += 1

    await db.flush()
    return rooms_created
