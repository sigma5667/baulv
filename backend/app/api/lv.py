from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import io

from app.db.session import get_db
from app.db.models.lv import Leistungsverzeichnis, Leistungsgruppe, Position
from app.db.models.project import Building, Floor, Room, Unit
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


# Keywords we use to identify a position as "wall-area based". The
# Wandberechnung service computes a single number (total net wall
# area across all rooms) and the sync endpoint fans it out into every
# position whose kurztext/langtext mentions any of these German
# construction terms. The list intentionally covers the four trades
# the product spec names: Malerarbeiten (anstrich/malerei), Tapezier-
# arbeiten (tapete/tapezier), Fliesenlegearbeiten (fliesen/wandfliese),
# Verputzarbeiten (putz/verputz). "wand" on its own catches generic
# "Wandbeschichtung" / "Wandverkleidung" positions the AI text
# generator may emit.
_WALL_POSITION_KEYWORDS: tuple[str, ...] = (
    "wand",           # wandfläche, wandanstrich, wandbeschichtung, …
    "tapet",          # tapete, tapezierarbeiten, tapezieren
    "anstrich",       # anstrich (wall painting)
    "malerei",        # malerei, malerarbeiten
    "fliese",         # fliesen, wandfliesen, fliesenarbeiten
    "verputz",        # verputz, verputzarbeiten
    "putz",           # putz (matches innenputz, glattputz, …)
)


def _is_wall_position(position: "Position") -> bool:
    """Return True iff this position's text looks like wall-area work.

    We check ``kurztext`` and ``langtext`` (both lowercased) against
    the keyword list. Also require ``einheit`` to be ``m2`` / ``m²``
    so we don't accidentally overwrite linear-metre ("lfm") or
    per-piece positions that happen to mention "Wand" in their text.
    """

    einheit = (position.einheit or "").lower().strip()
    # Accept "m2", "m²", "qm"; reject anything else.
    if einheit not in {"m2", "m²", "qm"}:
        return False
    haystack = " ".join(
        (position.kurztext or "", position.langtext or "")
    ).lower()
    return any(kw in haystack for kw in _WALL_POSITION_KEYWORDS)


@router.post("/{lv_id}/sync-wall-areas")
async def sync_wall_areas(
    lv_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Übertrage Netto-Wandflächen aus den Räumen in passende LV-Positionen.

    Summiert ``wall_area_net_m2`` über alle Räume des Projekts und
    schreibt den Wert in die ``menge`` jeder Wand-Position (m²,
    Schlagworte Wand/Tapete/Anstrich/Fliesen/Putz). Gesperrte
    Positionen (``is_locked``) werden übersprungen.

    Antwort:

    ```
    {
      "lv_id": ...,
      "total_wall_area_m2": 1234.56,
      "positions_updated": 3,
      "positions_skipped_locked": 1,
      "rooms_considered": 42
    }
    ```
    """
    lv = await verify_lv_owner(lv_id, user, db)

    # Load every room in the LV's project with its precomputed
    # wall-area cache. We don't re-run the calculator here — the
    # Wandberechnung page is where the user is expected to confirm
    # heights and trigger the bulk calc. This endpoint just reads
    # the cached value and fans it out.
    stmt = (
        select(Room)
        .join(Unit).join(Floor).join(Building)
        .where(Building.project_id == lv.project_id)
    )
    rooms = (await db.execute(stmt)).scalars().all()

    total_net = sum(
        float(r.wall_area_net_m2) for r in rooms if r.wall_area_net_m2 is not None
    )
    # Round to the 2-decimal precision the UI and LV share.
    total_net_rounded = round(total_net, 2)

    if not rooms:
        raise HTTPException(
            400,
            "Für dieses Projekt wurden noch keine Räume erfasst. Bitte "
            "zuerst die Plananalyse durchführen oder Räume manuell "
            "anlegen, bevor Wandflächen übernommen werden können.",
        )

    # Load the LV's positions through groups.
    pos_stmt = (
        select(Position)
        .join(Leistungsgruppe, Position.gruppe_id == Leistungsgruppe.id)
        .where(Leistungsgruppe.lv_id == lv_id)
    )
    positions = list((await db.execute(pos_stmt)).scalars().all())

    updated = 0
    skipped_locked = 0
    for pos in positions:
        if not _is_wall_position(pos):
            continue
        if pos.is_locked:
            skipped_locked += 1
            continue
        pos.menge = total_net_rounded
        updated += 1

    await db.flush()

    return {
        "lv_id": str(lv_id),
        "total_wall_area_m2": total_net_rounded,
        "positions_updated": updated,
        "positions_skipped_locked": skipped_locked,
        "rooms_considered": len(rooms),
    }


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
