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
    UploadFile,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ownership import verify_plan_owner, verify_project_owner
from app.auth import get_current_user
from app.config import settings
from app.db.models.plan import Plan
from app.db.models.user import User
from app.db.session import get_db
from app.plan_analysis.pipeline import PlanAnalysisError, analyze_plan
from app.schemas.plan import PlanResponse
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

    plan = Plan(
        project_id=project_id,
        filename=file.filename,
        file_path=str(file_path),
        file_size_bytes=actual_size,
        plan_type=plan_type,
    )
    db.add(plan)
    await db.flush()
    logger.info(
        "Plan uploaded: project=%s plan=%s file=%s bytes=%d",
        project_id,
        plan.id,
        file.filename,
        actual_size,
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
        return await analyze_plan(plan_id, db)
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
