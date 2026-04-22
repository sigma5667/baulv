from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import io

from app.db.session import get_db
from app.db.models.lv import Leistungsverzeichnis, Leistungsgruppe, Position
from app.db.models.user import User
from app.schemas.lv import LVCreate, LVUpdate, LVResponse, PositionUpdate, PositionResponse
from app.calculation_engine.engine import calculate_lv
from app.lv_generator.generator import generate_position_texts
from app.export.xlsx_exporter import export_lv_xlsx
from app.export.pdf_exporter import export_lv_pdf
from app.auth import get_current_user
from app.api.ownership import verify_project_owner, verify_lv_owner
from app.subscriptions import require_feature, has_feature

router = APIRouter()


@router.post("/projects/{project_id}/lv", response_model=LVResponse, status_code=201,
             tags=["Leistungsverzeichnis"])
async def create_lv(
    project_id: UUID,
    data: LVCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_owner(project_id, user, db)
    lv = Leistungsverzeichnis(project_id=project_id, **data.model_dump())
    db.add(lv)
    await db.flush()

    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv.id)
        .options(selectinload(Leistungsverzeichnis.gruppen))
    )
    result = await db.execute(stmt)
    return result.scalars().first()


@router.get("/projects/{project_id}/lv", response_model=list[LVResponse],
            tags=["Leistungsverzeichnis"])
async def list_lvs(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_owner(project_id, user, db)
    result = await db.execute(
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.project_id == project_id)
        .options(selectinload(Leistungsverzeichnis.gruppen))
        .order_by(Leistungsverzeichnis.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{lv_id}", response_model=LVResponse)
async def get_lv(
    lv_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_lv_owner(lv_id, user, db)
    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen)
            .selectinload(Leistungsgruppe.positionen)
            .selectinload(Position.berechnungsnachweise),
        )
    )
    result = await db.execute(stmt)
    lv = result.scalars().first()
    if not lv:
        raise HTTPException(404, "LV nicht gefunden")
    return lv


@router.put("/{lv_id}", response_model=LVResponse)
async def update_lv(
    lv_id: UUID,
    data: LVUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lv = await verify_lv_owner(lv_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(lv, key, value)
    await db.flush()
    return lv


@router.post("/{lv_id}/calculate")
async def run_calculation(
    lv_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the deterministic calculation engine for this LV."""
    await verify_lv_owner(lv_id, user, db)
    try:
        results = await calculate_lv(lv_id, db)
        return {
            "lv_id": str(lv_id),
            "positions_calculated": len(results),
            "total_measurement_lines": sum(len(r.measurement_lines) for r in results),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{lv_id}/generate-texts")
async def generate_texts(
    lv_id: UUID,
    user: User = Depends(require_feature("ai_position_generator")),
    db: AsyncSession = Depends(get_db),
):
    """Generate AI-powered position texts — requires Pro plan."""
    await verify_lv_owner(lv_id, user, db)

    # Pre-flight: AI text generation on an empty LV used to surface as a
    # 500 (downstream Claude call on a zero-position payload, or silent
    # no-op that the frontend couldn't distinguish from success). Check
    # position count up front and return a 400 with an actionable German
    # message, mirroring the ``/calculate`` endpoint's 400-on-ValueError
    # contract. The user needs to either run Plananalyse (rooms →
    # ``/calculate`` → positions) or add positions manually before this
    # endpoint has anything to work with.
    count_stmt = (
        select(func.count(Position.id))
        .join(Leistungsgruppe, Position.gruppe_id == Leistungsgruppe.id)
        .where(Leistungsgruppe.lv_id == lv_id)
    )
    position_count = (await db.execute(count_stmt)).scalar_one()
    if position_count == 0:
        raise HTTPException(
            400,
            "Bitte zuerst Räume über die Plananalyse erfassen oder manuell "
            "Positionen hinzufügen, bevor Sie AI-Texte generieren.",
        )

    try:
        updated = await generate_position_texts(lv_id, db)
        return {"lv_id": str(lv_id), "positions_updated": updated}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/positionen/{position_id}", response_model=PositionResponse)
async def update_position(
    position_id: UUID,
    data: PositionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Position)
        .where(Position.id == position_id)
        .options(selectinload(Position.berechnungsnachweise))
    )
    result = await db.execute(stmt)
    position = result.scalars().first()
    if not position:
        raise HTTPException(404, "Position nicht gefunden")

    # Verify ownership: Position -> Gruppe -> LV -> Project
    gruppe = await db.get(Leistungsgruppe, position.gruppe_id)
    if not gruppe:
        raise HTTPException(404, "Leistungsgruppe nicht gefunden")
    await verify_lv_owner(gruppe.lv_id, user, db)

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(position, key, value)
    if data.kurztext or data.langtext:
        position.text_source = "manual"
    await db.flush()
    return position


@router.post("/{lv_id}/export")
async def export_lv(
    lv_id: UUID,
    format: str = "xlsx",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export LV as Excel (Pro-gated) or PDF (available to all authenticated users).

    The pricing page lists PDF as a Basis-plan feature; the feature matrix
    (``app/subscriptions.py::get_feature_matrix``) already returns
    ``pdf_export: True`` unconditionally. The old implementation gated the
    whole route with ``require_feature("excel_export")``, which bounced
    Basis users with a 403 before the format dispatch could hand them off
    to the PDF branch. We now check the per-format plan requirement inside
    the dispatch so the gate matches the advertised pricing matrix.
    """
    await verify_lv_owner(lv_id, user, db)
    if format == "xlsx":
        # Excel remains Pro-only, matching ``FEATURE_REQUIREMENTS``.
        if not has_feature(user.subscription_plan, "excel_export"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Excel Export erfordert den Pro-Plan. "
                    "Bitte upgraden Sie Ihr Abonnement."
                ),
            )
        try:
            xlsx_bytes = await export_lv_xlsx(lv_id, db)
            return StreamingResponse(
                io.BytesIO(xlsx_bytes),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": f"attachment; filename=LV_{lv_id}.xlsx"
                },
            )
        except ValueError as e:
            raise HTTPException(404, str(e))
    if format == "pdf":
        # PDF is a Basis-tier feature — no plan gate, just ownership.
        try:
            pdf_bytes = await export_lv_pdf(lv_id, db)
            return StreamingResponse(
                io.BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"attachment; filename=LV_{lv_id}.pdf"
                },
            )
        except ValueError as e:
            raise HTTPException(404, str(e))
    raise HTTPException(
        400,
        f"Format '{format}' nicht unterstützt. Bitte 'xlsx' oder 'pdf' wählen.",
    )
