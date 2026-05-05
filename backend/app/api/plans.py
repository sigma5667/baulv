"""Plan upload + Claude Vision analysis endpoints.

Every failure mode is mapped to a specific German error message and a
sensible HTTP status code. The frontend relies on the status code to
decide what to show (upgrade banner for 403, validation hint for 400,
generic retry for 5xx) and on ``detail`` for the exact text. We never
raise a bare 500 with an opaque message — the analysis endpoint
surfaces ``PlanAnalysisError.detail`` directly so the user always sees
something actionable.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ownership import verify_plan_owner, verify_project_owner
from app.auth import get_current_user
from app.config import settings
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.plan import Plan
from app.db.models.project import Opening, Room
from app.db.models.user import User
from app.db.session import get_db
from app.plan_analysis.pipeline import PlanAnalysisError, analyze_plan
from app.schemas.plan import (
    PlanDeletionPreview,
    PlanDeletionResult,
    PlanResponse,
)
from app.services import analytics as analytics_service
from app.services import audit
from app.db.models.analytics import EVENT_PLAN_ANALYZED as _ANALYTICS_PLAN_ANALYZED
from app.subscriptions import require_feature

logger = logging.getLogger(__name__)

router = APIRouter()


# Filenames coming off the multipart upload are attacker-controlled.
# Strip anything that could escape the project's upload directory or
# cause weirdness on the filesystem. The UUID prefix we add later
# guarantees uniqueness regardless of what the user picked.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str | None) -> str:
    """Return a filesystem-safe filename derived from ``name``.

    Falls back to ``plan.pdf`` if the input is empty after sanitizing.
    Strips directory components, replaces any non-alphanumeric run with
    an underscore, and caps the length. We don't try to preserve the
    user's exact name — we only want something readable in the audit log.
    """
    if not name:
        return "plan.pdf"
    # Kill any path components; only keep the basename.
    base = Path(name).name
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._") or "plan.pdf"
    # 120 is plenty for display and avoids OS path-length surprises.
    return cleaned[:120]


@router.post(
    "/projects/{project_id}/plans",
    response_model=PlanResponse,
    status_code=201,
    tags=["Plans"],
)
async def upload_plan(
    project_id: UUID,
    file: UploadFile = File(...),
    plan_type: str = Query("grundriss"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a construction plan PDF.

    Validates: content type is PDF, size is within ``max_plan_file_mb``,
    filename is safe. The per-project upload directory is
    ``{upload_path}/{project_id}/`` and the stored file is prefixed
    with a short UUID so re-uploading the same name doesn't collide.
    """
    await verify_project_owner(project_id, user, db)

    # --- Validation ----------------------------------------------------
    # The user-facing rejection text is pinned so the frontend and
    # backend say the same thing. Mentioned both in the MIME check and
    # in the magic-byte check below.
    NOT_A_PDF = (
        "Nur PDF-Dateien sind erlaubt. Bitte konvertieren Sie Ihr Bild in "
        "eine PDF oder verwenden Sie einen Bauplan im PDF-Format."
    )

    if not file.filename:
        raise HTTPException(400, "Kein Dateiname angegeben.")

    # First-line defense: extension + MIME. This catches the obvious
    # case (image001.png) before we bother reading the file. Browsers
    # can be sloppy with MIME, so we accept either the correct MIME
    # *or* a .pdf extension. The magic-byte check below is the
    # authoritative verdict.
    ct = (file.content_type or "").lower()
    ext_ok = file.filename.lower().endswith(".pdf")
    mime_ok = ct in ("application/pdf", "application/x-pdf")
    # A file is a plausible PDF candidate only if *at least one* of
    # extension or MIME claims PDF. image001.png fails both.
    if not (ext_ok or mime_ok):
        raise HTTPException(400, NOT_A_PDF)

    max_bytes = settings.max_plan_file_mb * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            413,
            f"Die Datei ist zu groß ({file.size // (1024 * 1024)} MB). "
            f"Maximal {settings.max_plan_file_mb} MB pro Plan erlaubt.",
        )

    # Second-line defense (the real one): peek at the first bytes. Every
    # legitimate PDF starts with ``%PDF-`` per the PDF spec — there's no
    # way a PNG, JPEG, Word doc, or renamed-extension file makes it
    # past here. We read 8 bytes and seek back so the subsequent
    # streaming write still sees the full content.
    header = file.file.read(8)
    try:
        file.file.seek(0)
    except (AttributeError, OSError):
        # UploadFile's SpooledTemporaryFile should always seek, but
        # if an exotic transport doesn't, we can't recover — reject.
        logger.warning("Upload file stream not seekable; rejecting upload.")
        raise HTTPException(400, NOT_A_PDF)

    if not header.startswith(b"%PDF-"):
        logger.info(
            "Upload rejected as non-PDF: filename=%s ct=%s magic=%r",
            file.filename,
            ct,
            header[:8],
        )
        raise HTTPException(400, NOT_A_PDF)

    # --- Persist to disk ----------------------------------------------
    upload_dir = settings.upload_path / str(project_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(file.filename)
    # Short UUID prefix avoids collisions with prior uploads of the
    # same filename. ``uuid4().hex[:8]`` gives us 32 bits of entropy,
    # more than enough for per-project scope.
    stored_name = f"{uuid4().hex[:8]}_{safe_name}"
    file_path = upload_dir / stored_name

    try:
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except OSError as e:
        logger.exception("Failed to write plan upload to %s: %s", file_path, e)
        raise HTTPException(
            500, "Die Datei konnte nicht gespeichert werden. Bitte erneut versuchen."
        )

    # Double-check size post-write (in case file.size was None).
    actual_size = file_path.stat().st_size
    if actual_size > max_bytes:
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            413,
            f"Die Datei ist zu groß ({actual_size // (1024 * 1024)} MB). "
            f"Maximal {settings.max_plan_file_mb} MB pro Plan erlaubt.",
        )

    # v23.7 — extract page_count from the PDF metadata at upload time.
    # Pre-v23.7 we deferred this to the AI analysis step (the pipeline
    # writes ``plan.page_count = len(rendered_pages)`` after rendering),
    # which meant the plans-list UI rendered "? Seiten" for every plan
    # the user uploaded but hadn't yet analysed — unprofessional and a
    # constant pain-point in tester feedback. Reading the page count
    # here is dirt-cheap (PyMuPDF only parses the xref/trailer, doesn't
    # render anything) so we do it inline rather than as a background
    # task. ``page_count`` stays NULL on extraction failure so the UI
    # falls back to its existing graceful path.
    extracted_page_count: int | None = None
    try:
        import fitz  # PyMuPDF — same lib used by the analysis pipeline

        doc = fitz.open(str(file_path))
        try:
            extracted_page_count = doc.page_count
        finally:
            doc.close()
    except Exception as e:  # noqa: BLE001
        # Best-effort. A corrupt PDF will fail the analysis step
        # later with a friendly German message; for the upload step
        # we don't want a metadata read to abort the whole request.
        logger.warning(
            "Plan upload: page_count extraction failed plan_file=%s err=%s",
            file_path,
            e,
        )

    plan = Plan(
        project_id=project_id,
        filename=file.filename,
        file_path=str(file_path),
        file_size_bytes=actual_size,
        page_count=extracted_page_count,
        plan_type=plan_type,
    )
    db.add(plan)
    await db.flush()
    logger.info(
        "Plan uploaded: project=%s plan=%s file=%s bytes=%d pages=%s",
        project_id,
        plan.id,
        file.filename,
        actual_size,
        extracted_page_count if extracted_page_count is not None else "unknown",
    )
    return plan


@router.get(
    "/projects/{project_id}/plans",
    response_model=list[PlanResponse],
    tags=["Plans"],
)
async def list_plans(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_owner(project_id, user, db)
    result = await db.execute(
        select(Plan).where(Plan.project_id == project_id).order_by(Plan.created_at.desc())
    )
    return result.scalars().all()


@router.post("/{plan_id}/analyze", tags=["Plans"])
async def trigger_analysis(
    plan_id: UUID,
    user: User = Depends(require_feature("ai_plan_analysis")),
    db: AsyncSession = Depends(get_db),
):
    """Trigger Claude Vision analysis of a plan.

    Requires the Pro plan (enforced by ``require_feature``). The
    ``PlanAnalysisError`` machinery in the pipeline guarantees every
    failure mode comes back with a German, user-safe message; we
    surface it here as a 422 (unprocessable) so the frontend can
    distinguish it from auth failures, quota errors, and unknown 500s.
    """
    plan = await verify_plan_owner(plan_id, user, db)
    # Rich logging — when a user reports "the button does nothing",
    # these lines are the first thing an operator looks at. Includes
    # user plan (to correlate with feature-gate decisions), plan
    # status (to spot retries), filename, and on-disk size.
    logger.info(
        "Plan analysis trigger: plan=%s user=%s plan_subscription=%s status=%s "
        "filename=%s file_size=%s",
        plan_id,
        user.id,
        user.subscription_plan,
        plan.analysis_status,
        plan.filename,
        plan.file_size_bytes,
    )

    try:
        result = await analyze_plan(plan_id, db)
        # v23.8 — analytics signal for plan_analyzed. Best-effort:
        # the analyse call itself succeeded, the recorder is
        # gated on consent and fail-soft, so we never raise here.
        try:
            pages_analyzed = int(result.get("pages_analyzed", 0)) if isinstance(result, dict) else 0
            rooms_extracted = int(result.get("rooms_extracted", 0)) if isinstance(result, dict) else 0
            await analytics_service.record_event(
                db,
                event_type=_ANALYTICS_PLAN_ANALYZED,
                user=user,
                event_data={
                    "pages": pages_analyzed,
                    "rooms_extracted": rooms_extracted,
                },
            )
        except Exception:  # noqa: BLE001
            # Belt-and-braces. The recorder swallows internally;
            # this catch handles anything escaping (e.g. a future
            # refactor that changes the result-shape contract).
            logger.exception(
                "analytics.plan_analyzed failed plan_id=%s — continuing",
                plan_id,
            )
        return result
    except PlanAnalysisError as e:
        # Already German and user-safe. 422 ("unprocessable entity")
        # is the closest semantic match: the request was valid but we
        # couldn't produce the requested result.
        raise HTTPException(status_code=422, detail=e.detail)
    except Exception as e:  # noqa: BLE001
        # Shouldn't happen — pipeline.analyze_plan wraps unknown errors
        # in PlanAnalysisError — but belt-and-braces.
        logger.exception("Unexpected analyze error for plan %s: %s", plan_id, e)
        raise HTTPException(
            status_code=500,
            detail=(
                "Bei der KI-Analyse ist ein unerwarteter Fehler aufgetreten. "
                "Bitte versuchen Sie es erneut oder kontaktieren Sie den Support."
            ),
        )


@router.get("/{plan_id}", response_model=PlanResponse, tags=["Plans"])
async def get_plan(
    plan_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await verify_plan_owner(plan_id, user, db)
    return plan


# ---------------------------------------------------------------------------
# v23 — Plan deletion
# ---------------------------------------------------------------------------


async def _deletion_preview(plan: Plan, db: AsyncSession) -> PlanDeletionPreview:
    """Count what would be affected if this plan were deleted.

    Surfaced both via the standalone preview endpoint (so the
    confirmation dialog can show numbers before the user commits) and
    used internally by ``delete_plan`` to log a precise audit entry.

    Three counts the user genuinely cares about:
      * ``rooms_linked``       — how many rooms originated from this
                                 plan (``rooms.plan_id == plan.id``).
      * ``rooms_manual_among_linked`` — subset that the user has
                                 since edited; surfaced so the dialog
                                 can warn before nuking work.
      * ``openings_linked``    — opening rows belonging to those rooms
                                 (cascade-delete with their parent).
      * ``proofs_linked``      — Berechnungsnachweis rows pointing to
                                 those rooms. Big deal because their
                                 LV-position parents keep their cached
                                 ``menge`` while losing the proof.
    """

    # rooms_linked
    rooms_linked = (
        await db.execute(
            select(func.count())
            .select_from(Room)
            .where(Room.plan_id == plan.id)
        )
    ).scalar_one()

    rooms_manual_among_linked = (
        await db.execute(
            select(func.count())
            .select_from(Room)
            .where(Room.plan_id == plan.id, Room.source == "manual")
        )
    ).scalar_one()

    # openings on those rooms — join for accuracy.
    openings_linked = (
        await db.execute(
            select(func.count())
            .select_from(Opening)
            .join(Room, Opening.room_id == Room.id)
            .where(Room.plan_id == plan.id)
        )
    ).scalar_one()

    # Berechnungsnachweise on those rooms.
    proofs_linked = (
        await db.execute(
            select(func.count())
            .select_from(Berechnungsnachweis)
            .join(Room, Berechnungsnachweis.room_id == Room.id)
            .where(Room.plan_id == plan.id)
        )
    ).scalar_one()

    return PlanDeletionPreview(
        plan_id=plan.id,
        filename=plan.filename,
        rooms_linked=int(rooms_linked or 0),
        rooms_manual_among_linked=int(rooms_manual_among_linked or 0),
        openings_linked=int(openings_linked or 0),
        proofs_linked=int(proofs_linked or 0),
    )


@router.get(
    "/{plan_id}/deletion-preview",
    response_model=PlanDeletionPreview,
    tags=["Plans"],
)
async def plan_deletion_preview(
    plan_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return what would be deleted if the user clicks "Plan löschen".

    The frontend confirmation dialog issues this GET before showing
    its two buttons so the wording can be specific:
    "8 Räume verknüpft, davon 3 manuell überarbeitet — auch löschen?"
    """
    plan = await verify_plan_owner(plan_id, user, db)
    return await _deletion_preview(plan, db)


@router.delete("/{plan_id}", response_model=PlanDeletionResult, tags=["Plans"])
async def delete_plan(
    plan_id: UUID,
    request: Request,
    delete_rooms: bool = Query(
        False,
        description=(
            "Wenn true: alle vom Plan extrahierten Räume und ihre "
            "Öffnungen + Berechnungsnachweise werden mitgelöscht. "
            "Wenn false (Default): Räume bleiben erhalten, ihr "
            "plan_id-Verweis wird über die FK auf NULL gesetzt."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a plan and optionally its extracted rooms.

    Order of operations
    -------------------
    1. Tenancy check via ``verify_plan_owner``.
    2. Snapshot the deletion-impact counts via ``_deletion_preview``
       — captured BEFORE we delete anything so the audit log records
       what was actually wiped.
    3. If ``delete_rooms=True``: delete the rooms first. Their
       openings (``cascade='all, delete-orphan'``) and
       Berechnungsnachweise (``ON DELETE CASCADE``) follow.
    4. Delete the Plan row. The Room→Plan FK is ``ON DELETE SET
       NULL`` (migration 017), so any rooms still referencing the
       plan have their ``plan_id`` cleared automatically.
    5. Best-effort filesystem unlink of the PDF on disk. We log on
       failure but never roll back — the DB row is gone, the file
       can be cleaned up by a janitor cron later.
    6. Audit-log entry with the captured counts.

    The audit happens at the end so ``rooms_deleted`` etc. reflect
    what actually flushed. Any earlier failure rolls back without
    leaving a misleading "we deleted N" entry.
    """
    plan = await verify_plan_owner(plan_id, user, db)

    # Snapshot the impact BEFORE we change anything — the audit log
    # records what actually went away on this single transaction.
    preview = await _deletion_preview(plan, db)

    rooms_deleted = 0
    openings_deleted = 0
    proofs_deleted = 0

    if delete_rooms:
        # Delete the rooms; openings + proofs follow via cascade.
        # We use a bulk DELETE statement for the actual write so we
        # don't have to load every Room into the session — but we
        # still capture the counts from the pre-snapshot.
        await db.execute(
            delete(Room).where(Room.plan_id == plan.id)
        )
        rooms_deleted = preview.rooms_linked
        openings_deleted = preview.openings_linked
        proofs_deleted = preview.proofs_linked

    # Plan row goes last. The Room→Plan FK is SET NULL so rooms we
    # deliberately kept (delete_rooms=False) lose their link
    # automatically.
    await db.delete(plan)
    await db.flush()

    # Best-effort file cleanup. We swallow any OSError so a
    # missing/locked file doesn't roll back the DB delete.
    file_unlinked = False
    if plan.file_path:
        try:
            os.unlink(plan.file_path)
            file_unlinked = True
        except FileNotFoundError:
            # Already gone — count as "unlinked" so the audit log
            # doesn't false-flag this as a leak.
            file_unlinked = True
        except OSError as e:
            logger.warning(
                "Plan delete: file unlink failed plan=%s path=%s err=%s",
                plan_id,
                plan.file_path,
                e,
            )

    # Audit — single ``plan.deleted`` event, meta differentiates the
    # cascade choice and records the actual impact.
    await audit.log_event(
        db,
        event_type=audit.EVENT_PLAN_DELETED,
        user_id=user.id,
        request=request,
        meta={
            "plan_id": str(plan_id),
            "filename": plan.filename,
            "delete_rooms": delete_rooms,
            "rooms_deleted": rooms_deleted,
            "openings_deleted": openings_deleted,
            "proofs_deleted": proofs_deleted,
            "file_unlinked": file_unlinked,
        },
    )
    await db.commit()

    logger.info(
        "Plan deleted plan=%s user=%s delete_rooms=%s rooms=%d openings=%d proofs=%d",
        plan_id,
        user.id,
        delete_rooms,
        rooms_deleted,
        openings_deleted,
        proofs_deleted,
    )

    return PlanDeletionResult(
        plan_id=plan_id,
        delete_rooms=delete_rooms,
        rooms_deleted=rooms_deleted,
        openings_deleted=openings_deleted,
        proofs_deleted=proofs_deleted,
        file_unlinked=file_unlinked,
    )
