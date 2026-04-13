import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.db.models.plan import Plan
from app.db.models.user import User
from app.schemas.plan import PlanResponse
from app.plan_analysis.pipeline import analyze_plan
from app.auth import get_current_user
from app.api.ownership import verify_project_owner, verify_plan_owner
from app.subscriptions import require_feature

router = APIRouter()


@router.post("/projects/{project_id}/plans", response_model=PlanResponse, status_code=201,
             tags=["Plans"])
async def upload_plan(
    project_id: UUID,
    file: UploadFile = File(...),
    plan_type: str = Query("grundriss"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a construction plan PDF."""
    await verify_project_owner(project_id, user, db)

    upload_dir = settings.upload_path / str(project_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    plan = Plan(
        project_id=project_id,
        filename=file.filename,
        file_path=str(file_path),
        file_size_bytes=file.size,
        plan_type=plan_type,
    )
    db.add(plan)
    await db.flush()
    return plan


@router.get("/projects/{project_id}/plans", response_model=list[PlanResponse],
            tags=["Plans"])
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
    """Trigger Claude Vision analysis of a plan — requires Pro plan."""
    await verify_plan_owner(plan_id, user, db)
    try:
        result = await analyze_plan(plan_id, db)
        return result
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)}")


@router.get("/{plan_id}", response_model=PlanResponse, tags=["Plans"])
async def get_plan(
    plan_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await verify_plan_owner(plan_id, user, db)
    return plan
