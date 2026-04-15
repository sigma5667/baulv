import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    get_current_session,
    get_current_user,
    hash_password,
    issue_session,
    verify_password,
)
from app.db.models.audit import AuditLogEntry
from app.db.models.project import Project
from app.db.models.session import UserSession
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.user import (
    AccountDeletionRequest,
    AuditLogEntryResponse,
    PasswordChangeRequest,
    PasswordResetRequest,
    PrivacySettingsUpdate,
    SessionResponse,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    UserUpdate,
)
from app.services.audit import (
    EVENT_ACCOUNT_DELETED,
    EVENT_DATA_EXPORTED,
    EVENT_LOGIN,
    EVENT_LOGIN_FAILED,
    EVENT_PASSWORD_CHANGED,
    EVENT_PRIVACY_UPDATED,
    EVENT_REGISTER,
    EVENT_SESSION_REVOKED,
    EVENT_SESSIONS_REVOKED_ALL,
    log_event,
)
from app.services.dsgvo import delete_user_account, export_user_data
from app.subscriptions import get_feature_matrix

router = APIRouter()


# Literal confirmation string the client must echo back when deleting an
# account. Kept as a module constant so frontend and backend can stay in
# sync via a single grep.
DELETE_CONFIRMATION_PHRASE = "LÖSCHEN"


# ---------------------------------------------------------------------------
# Register / login / profile
# ---------------------------------------------------------------------------


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
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

    token = await issue_session(db, user, request)
    await log_event(db, event_type=EVENT_REGISTER, user_id=user.id, request=request)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    data: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == data.email.lower().strip()))
    user = result.scalars().first()
    if not user or not verify_password(data.password, user.password_hash):
        # Log failed attempts (with email but not password) so users can
        # see attempted intrusions in their audit view. user_id is None
        # because the attempt may be for a non-existent account.
        await log_event(
            db,
            event_type=EVENT_LOGIN_FAILED,
            user_id=user.id if user else None,
            request=request,
            meta={"email": data.email.lower().strip()},
        )
        raise HTTPException(status_code=401, detail="Ungültige E-Mail oder Passwort.")

    token = await issue_session(db, user, request)
    await log_event(db, event_type=EVENT_LOGIN, user_id=user.id, request=request)
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


# ---------------------------------------------------------------------------
# DSGVO: password change, data export, account deletion
# ---------------------------------------------------------------------------


@router.post("/me/password", status_code=204)
async def change_password(
    data: PasswordChangeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    current_session: UserSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
):
    """Change the caller's password and revoke all other sessions.

    Re-authentication via ``current_password`` is required — a valid
    bearer token alone is not sufficient. On success we revoke every
    session for this user *except* the one making the request: the
    user stays logged in here, but a thief with a stolen token from
    another device is kicked off.
    """
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aktuelles Passwort ist falsch.",
        )
    if len(data.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Neues Passwort muss mindestens 8 Zeichen lang sein.",
        )
    user.password_hash = hash_password(data.new_password)

    # Revoke every other live session for this user. Current session
    # stays active so the user doesn't have to log in again on the
    # device where they just changed their password.
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.id != current_session.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.flush()

    await log_event(
        db,
        event_type=EVENT_PASSWORD_CHANGED,
        user_id=user.id,
        request=request,
    )
    return Response(status_code=204)


@router.get("/me/export")
async def export_my_data(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Art. 20 DSGVO — Right to data portability.

    Streams a JSON file containing every personal-data row we hold for
    the caller. Format is versioned via ``schema_version`` inside the
    payload so that future exports remain backward compatible.
    """
    payload = await export_user_data(user, db)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"baulv-export-{timestamp}.json"

    await log_event(
        db,
        event_type=EVENT_DATA_EXPORTED,
        user_id=user.id,
        request=request,
        meta={"size_bytes": len(body)},
    )

    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Explicitly disable caching — this is personal data.
            "Cache-Control": "no-store",
        },
    )


@router.post("/me/delete", status_code=204)
async def delete_my_account(
    data: AccountDeletionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Art. 17 DSGVO — Right to erasure.

    Irreversibly deletes the caller's account and every associated
    record. Requires both the current password and the literal string
    ``LÖSCHEN`` as a confirmation payload to guard against accidental
    and malicious deletions (stolen tokens, UI bugs).
    """
    if data.confirmation != DELETE_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bitte geben Sie '{DELETE_CONFIRMATION_PHRASE}' zur Bestätigung ein.",
        )
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwort ist falsch.",
        )

    # Write the audit entry BEFORE deleting the user. The audit table
    # FK uses ON DELETE SET NULL so the row will survive the cascade,
    # but we want the event captured while we still know the user ID.
    user_id = user.id
    user_email = user.email
    await log_event(
        db,
        event_type=EVENT_ACCOUNT_DELETED,
        user_id=user_id,
        request=request,
        meta={"email": user_email},
    )

    await delete_user_account(user, db)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Privacy settings
# ---------------------------------------------------------------------------


@router.put("/me/privacy", response_model=UserResponse)
async def update_privacy_settings(
    data: PrivacySettingsUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the caller's privacy-related flags.

    Currently the only flag is the marketing email opt-in. The AI
    processing notice is display-only on the frontend — there is no
    opt-out, since core functionality (plan analysis, chat, LV text
    generation) *requires* sending data to the Claude API, and we
    document that at signup in the Datenschutz page.
    """
    changes: dict[str, object] = {}
    if data.marketing_email_opt_in is not None:
        if user.marketing_email_opt_in != data.marketing_email_opt_in:
            changes["marketing_email_opt_in"] = data.marketing_email_opt_in
        user.marketing_email_opt_in = data.marketing_email_opt_in

    await db.flush()
    await db.refresh(user)

    if changes:
        await log_event(
            db,
            event_type=EVENT_PRIVACY_UPDATED,
            user_id=user.id,
            request=request,
            meta=changes,
        )

    return UserResponse.model_validate(user)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@router.get("/me/sessions", response_model=list[SessionResponse])
async def list_my_sessions(
    user: User = Depends(get_current_user),
    current_session: UserSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
):
    """List all sessions (active and recently revoked) for the user.

    Revoked sessions are kept in the response so the user can see
    "this device was signed out on X" — they're greyed out in the
    UI but not silently dropped.
    """
    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .order_by(UserSession.last_used_at.desc())
    )
    sessions = result.scalars().all()

    return [
        SessionResponse(
            id=s.id,
            user_agent=s.user_agent,
            ip_address=str(s.ip_address) if s.ip_address else None,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            expires_at=s.expires_at,
            revoked_at=s.revoked_at,
            is_current=(s.id == current_session.id),
        )
        for s in sessions
    ]


@router.delete("/me/sessions/{session_id}", status_code=204)
async def revoke_my_session(
    session_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    current_session: UserSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
):
    """Revoke one specific session.

    Revoking the current session is allowed and acts as a logout: the
    next request with the same token will 401 and the client will
    bounce to the login page.
    """
    target = await db.get(UserSession, session_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status_code=404, detail="Sitzung nicht gefunden.")

    if target.revoked_at is None:
        target.revoked_at = datetime.now(timezone.utc)
        await db.flush()

    await log_event(
        db,
        event_type=EVENT_SESSION_REVOKED,
        user_id=user.id,
        request=request,
        meta={
            "session_id": str(session_id),
            "was_current": target.id == current_session.id,
        },
    )
    return Response(status_code=204)


@router.post("/me/sessions/revoke-others", status_code=204)
async def revoke_other_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    current_session: UserSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
):
    """Revoke every session for the caller except the current one."""
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.id != current_session.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.flush()

    await log_event(
        db,
        event_type=EVENT_SESSIONS_REVOKED_ALL,
        user_id=user.id,
        request=request,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/me/audit-log", response_model=list[AuditLogEntryResponse])
async def list_my_audit_log(
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the user's own audit log, newest first.

    Capped at ``limit`` entries (default 100, max 500). A full export
    of the audit log is included in the Art. 20 data export.
    """
    limit = max(1, min(limit, 500))
    result = await db.execute(
        select(AuditLogEntry)
        .where(AuditLogEntry.user_id == user.id)
        .order_by(AuditLogEntry.created_at.desc())
        .limit(limit)
    )
    # Build responses manually — the INET column comes back as an
    # ipaddress object from asyncpg and pydantic's ``str | None`` won't
    # coerce it automatically.
    return [
        AuditLogEntryResponse(
            id=e.id,
            event_type=e.event_type,
            meta=e.meta,
            ip_address=str(e.ip_address) if e.ip_address is not None else None,
            user_agent=e.user_agent,
            created_at=e.created_at,
        )
        for e in result.scalars().all()
    ]
