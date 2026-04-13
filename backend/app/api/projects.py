from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.project import Project
from app.db.models.user import User
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse
from app.auth import get_current_user
from app.subscriptions import check_project_limit

router = APIRouter()


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List only the current user's projects."""
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user.id)
        .order_by(Project.updated_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a project — enforces subscription project limit."""
    # Check project limit for the user's plan
    count_result = await db.execute(
        select(func.count(Project.id)).where(Project.user_id == user.id)
    )
    current_count = count_result.scalar() or 0

    if not check_project_limit(user.subscription_plan, current_count):
        raise HTTPException(
            403,
            f"Projektlimit erreicht. Ihr {user.subscription_plan.title()}-Plan erlaubt maximal {current_count} Projekte. Bitte upgraden Sie Ihr Abonnement.",
        )

    project = Project(user_id=user.id, **data.model_dump())
    db.add(project)
    await db.flush()
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Projekt nicht gefunden")
    if project.user_id != user.id:
        raise HTTPException(403, "Zugriff verweigert")
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    data: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Projekt nicht gefunden")
    if project.user_id != user.id:
        raise HTTPException(403, "Zugriff verweigert")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    await db.flush()
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Projekt nicht gefunden")
    if project.user_id != user.id:
        raise HTTPException(403, "Zugriff verweigert")
    await db.delete(project)
    await db.flush()
