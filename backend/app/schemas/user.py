from datetime import datetime
from typing import Any
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
    marketing_email_opt_in: bool
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


class PasswordChangeRequest(BaseModel):
    """Change the current user's password.

    Requires the current password as a re-auth step — we do not trust
    the bearer token alone for sensitive account changes, since a
    stolen token shouldn't be enough to lock the real owner out.
    """

    current_password: str
    new_password: str


class AccountDeletionRequest(BaseModel):
    """Confirmation payload for DSGVO Art. 17 account deletion.

    Two barriers to accidental / malicious deletion:

    * ``password`` — proves the caller still knows the secret, not just
      that they have a valid bearer token. Protects against stolen tokens
      and shared devices.
    * ``confirmation`` — the user must type the literal string
      ``LÖSCHEN`` (checked server-side). Protects against UI bugs that
      fire a DELETE request without an explicit user action.
    """

    password: str
    confirmation: str


# ---------------------------------------------------------------------------
# Privacy settings (marketing consent etc.)
# ---------------------------------------------------------------------------

class PrivacySettingsUpdate(BaseModel):
    """Partial update for privacy-related user flags.

    All fields optional — only fields present in the request body are
    written back. This lets the frontend toggle one switch at a time
    without having to re-submit everything.
    """

    marketing_email_opt_in: bool | None = None


# ---------------------------------------------------------------------------
# Session management (Art. 32 — traceable sessions)
# ---------------------------------------------------------------------------

class SessionResponse(BaseModel):
    id: UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    # Server-assigned flag indicating which row corresponds to the token
    # used to make the /sessions request. The frontend uses this to
    # label one row as "this device" and to hide its revoke button.
    is_current: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLogEntryResponse(BaseModel):
    id: UUID
    event_type: str
    meta: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
