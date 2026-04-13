from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.user import User
from app.db.models.project import Project
from app.schemas.user import (
    UserRegister, UserLogin, UserUpdate, UserResponse,
    TokenResponse, PasswordResetRequest,
)
from app.auth import hash_password, verify_password, create_access_token, get_current_user
from app.subscriptions import get_feature_matrix

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == data.email.lower().strip()))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Diese E-Mail-Adresse ist bereits registriert.")

    user = User(
        email=data.email.lower().strip(),
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        company_name=data.company_name,
        subscription_plan="basis",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email.lower().strip()))
    user = result.scalars().first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Ungültige E-Mail oder Passwort.")
    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return UserResponse.model_validate(user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.company_name is not None:
        user.company_name = data.company_name
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.get("/me/features")
async def get_my_features(user: User = Depends(get_current_user)):
    return get_feature_matrix(user.subscription_plan)


@router.get("/me/usage")
async def get_my_usage(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(func.count(Project.id)).where(Project.user_id == user.id)
    )
    project_count = result.scalar() or 0
    return {
        "project_count": project_count,
        "project_limit": 3 if user.subscription_plan == "basis" else None,
    }


@router.post("/password-reset")
async def request_password_reset(data: PasswordResetRequest):
    # Placeholder — in production, send an email with a reset link
    return {"message": "Falls ein Konto mit dieser E-Mail existiert, wurde eine E-Mail mit Anweisungen zum Zurücksetzen des Passworts gesendet."}
