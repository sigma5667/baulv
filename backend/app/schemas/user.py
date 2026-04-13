from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserRegister(BaseModel):
    email: str
    password: str
    full_name: str
    company_name: str | None = None


class UserLogin(BaseModel):
    email: str
    password: str


class UserUpdate(BaseModel):
    full_name: str | None = None
    company_name: str | None = None


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    company_name: str | None
    subscription_plan: str
    stripe_customer_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str
