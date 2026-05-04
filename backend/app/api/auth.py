import json
from datetime import datetime, timedelta, timezone
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
from app.db.models.password_reset_token import PasswordResetToken
from app.db.models.project import Project
from app.db.models.session import UserSession
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.user import (
    AccountDeletionRequest,
    AuditLogEntryResponse,
    ConsentRefreshRequest,
    LegalVersionsResponse,
    PasswordChangeRequest,
    PasswordResetConfirm,
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
    EVENT_PASSWORD_RESET_COMPLETED,
    EVENT_PASSWORD_RESET_REQUESTED,
    EVENT_PRIVACY_UPDATED,
    EVENT_REGISTER,
    EVENT_SESSION_REVOKED,
    EVENT_SESSIONS_REVOKED_ALL,
    log_event,
)
from app.services.email import send_password_reset_email
from app.services.password_reset import (
    mark_token_used,
    mint_reset_token,
    verify_reset_token,
)
from app.services.consent import (
    record_consent_refresh,
    record_marketing_optin_change,
    record_registration_consent,
)
from app.services.dsgvo import delete_user_account, export_user_data
from app.legal_versions import (
    PRIVACY_POLICY_DATE,
    PRIVACY_POLICY_VERSION,
    TERMS_DATE,
    TERMS_VERSION,
)
from app.config import settings
from app.subscriptions import BETA_PROJECT_LIMIT_SENTINEL, get_feature_matrix

router = APIRouter()


# Literal confirmation string the client must echo back when deleting an
# account. Kept as a module constant so frontend and backend can stay in
# sync via a single grep.
DELETE_CONFIRMATION_PHRASE = "LÖSCHEN"


def _user_response(user: User) -> UserResponse:
    """Build a ``UserResponse`` including the canonical legal-version
    pins from ``app.legal_versions``.

    ``UserResponse`` carries four version fields (``accepted_*`` from
    the user row, ``required_*`` from the server constants) so the
    SPA can compute ``needs_consent_refresh`` without an extra
    ``GET /api/legal/versions`` round-trip on every page load.
    Centralising the construction here keeps every endpoint in
    lock-step — adding a new field later is one diff, not five.
    """
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        company_name=user.company_name,
        subscription_plan=user.subscription_plan,
        stripe_customer_id=user.stripe_customer_id,
        marketing_email_opt_in=user.marketing_email_opt_in,
        accepted_privacy_version=user.current_privacy_version,
        accepted_terms_version=user.current_terms_version,
        required_privacy_version=PRIVACY_POLICY_VERSION,
        required_terms_version=TERMS_VERSION,
        created_at=user.created_at,
    )


# ---------------------------------------------------------------------------
# Register / login / profile
# ---------------------------------------------------------------------------


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account with DSGVO Art. 7 consent capture.

    The request body must carry the version strings of the privacy
    policy and terms-of-service the user just saw. We refuse with
    409 if those don't match what the server currently serves —
    a stale tab can't sneak a user in under an outdated policy.

    Two atomic side-effects fire on success: the User row gets
    ``current_privacy_version`` / ``current_terms_version`` /
    ``marketing_email_opt_in`` set, AND a row in
    ``consent_snapshots`` records the moment + IP + UA forensically.
    Both writes share the surrounding transaction, so an audit
    failure rolls back the registration too — consent evidence is
    not best-effort.
    """
    # Version-mismatch guard. The frontend reads the canonical
    # versions from ``GET /api/legal/versions`` (or
    # ``/api/auth/me``'s ``required_*`` fields) and ships them back
    # here on submit. If the user kept a tab open across a privacy
    # bump, those strings won't match — better a clean 409 ("please
    # reload and re-accept") than registering them under stale
    # text.
    if data.accepted_privacy_version != PRIVACY_POLICY_VERSION:
        raise HTTPException(
            status_code=409,
            detail=(
                "Die Datenschutzerklärung wurde aktualisiert. "
                "Bitte laden Sie die Seite neu und akzeptieren Sie "
                "die aktuelle Version."
            ),
        )
    if data.accepted_terms_version != TERMS_VERSION:
        raise HTTPException(
            status_code=409,
            detail=(
                "Die AGB wurden aktualisiert. Bitte laden Sie die "
                "Seite neu und akzeptieren Sie die aktuelle Version."
            ),
        )

    existing = await db.execute(
        select(User).where(User.email == data.email.lower().strip())
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail="Diese E-Mail-Adresse ist bereits registriert.",
        )

    user = User(
        email=data.email.lower().strip(),
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        company_name=data.company_name,
        subscription_plan="basis",
        marketing_email_opt_in=data.marketing_optin,
        current_privacy_version=PRIVACY_POLICY_VERSION,
        current_terms_version=TERMS_VERSION,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # DSGVO Art. 7 evidence row. Bound to the same transaction as
    # the user write — if either fails, both roll back.
    await record_registration_consent(
        db,
        user_id=user.id,
        privacy_version=PRIVACY_POLICY_VERSION,
        terms_version=TERMS_VERSION,
        marketing_optin=data.marketing_optin,
        request=request,
    )

    token = await issue_session(db, user, request)
    await log_event(
        db, event_type=EVENT_REGISTER, user_id=user.id, request=request,
        meta={"marketing_optin": data.marketing_optin},
    )
    return TokenResponse(access_token=token, user=_user_response(user))


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
    return TokenResponse(access_token=token, user=_user_response(user))


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return _user_response(user)


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
    return _user_response(user)


@router.get("/legal/versions", response_model=LegalVersionsResponse)
async def get_legal_versions():
    """Public endpoint surfacing the canonical legal-document
    versions the server currently serves.

    Used by the SPA's registration page so the consent checkboxes
    can label themselves with the right version + date
    ("Datenschutzerklärung Version 1.0 vom 27.04.2026") and the
    payload can ship the matching version strings back on submit.

    Open to anonymous callers — pre-registration the user has no
    auth token, but they need this data to render the form.
    """
    return LegalVersionsResponse(
        privacy_version=PRIVACY_POLICY_VERSION,
        privacy_date=PRIVACY_POLICY_DATE,
        terms_version=TERMS_VERSION,
        terms_date=TERMS_DATE,
    )


@router.post("/me/consent/refresh", response_model=UserResponse)
async def refresh_consent(
    data: ConsentRefreshRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-record consent after a privacy or terms update.

    Fired by the SPA's ``ConsentRefreshModal`` when an authenticated
    user re-accepts the updated documents. Like ``register``, both
    version strings must match the canonical server pins; mismatch
    is a 409 ("reload, the policy changed again").

    Three side-effects under one transaction:

      1. ``users.current_privacy_version`` /
         ``current_terms_version`` get updated to the new pins.
      2. ``users.marketing_email_opt_in`` is set to whatever the
         modal's checkbox is on submit. We track this even on a
         consent-refresh because the user might use the moment to
         change their mind about marketing — the modal exposes the
         same checkbox the registration form did.
      3. A ``consent_snapshots`` row records the moment, with
         ``event_type`` chosen from whichever document actually
         changed (``privacy_update`` or ``terms_update``).

    If the user accepted exactly the version they already had on
    file (no document changed since their last accept), we still
    write a snapshot — useful as forensic evidence ("Maria
    confirmed her consent on date X") even when the content
    didn't move.
    """
    if data.accepted_privacy_version != PRIVACY_POLICY_VERSION:
        raise HTTPException(
            status_code=409,
            detail=(
                "Die Datenschutzerklärung wurde inzwischen erneut "
                "aktualisiert. Bitte laden Sie die Seite neu."
            ),
        )
    if data.accepted_terms_version != TERMS_VERSION:
        raise HTTPException(
            status_code=409,
            detail=(
                "Die AGB wurden inzwischen erneut aktualisiert. "
                "Bitte laden Sie die Seite neu."
            ),
        )

    privacy_changed = user.current_privacy_version != PRIVACY_POLICY_VERSION
    terms_changed = user.current_terms_version != TERMS_VERSION

    user.current_privacy_version = PRIVACY_POLICY_VERSION
    user.current_terms_version = TERMS_VERSION
    user.marketing_email_opt_in = data.marketing_optin
    await db.flush()

    await record_consent_refresh(
        db,
        user_id=user.id,
        privacy_version=PRIVACY_POLICY_VERSION,
        terms_version=TERMS_VERSION,
        marketing_optin=data.marketing_optin,
        privacy_changed=privacy_changed,
        terms_changed=terms_changed,
        request=request,
    )
    # Existing audit-log channel still records the privacy update
    # event, with a richer meta payload than v23.1.
    await log_event(
        db,
        event_type=EVENT_PRIVACY_UPDATED,
        user_id=user.id,
        request=request,
        meta={
            "privacy_changed": privacy_changed,
            "terms_changed": terms_changed,
            "marketing_optin": data.marketing_optin,
        },
    )

    await db.refresh(user)
    return _user_response(user)


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
    # Beta override: lift the project limit for every user. We surface
    # 999 (same sentinel the feature matrix uses) instead of None so
    # the frontend's "x / y Projekte" counter still renders a finite
    # quota for testers.
    if settings.beta_unlock_all_features:
        project_limit: int | None = BETA_PROJECT_LIMIT_SENTINEL
    else:
        project_limit = 3 if user.subscription_plan == "basis" else None
    return {
        "project_count": project_count,
        "project_limit": project_limit,
    }


# ---------------------------------------------------------------------------
# Password reset (DS-3 / v23.4)
# ---------------------------------------------------------------------------

# Per-email request budget. The token rows we already write per
# request are the rate-limit state — we count rows for the user_id
# in the last hour and refuse to mint a new one beyond this cap.
# Three is a kindness ceiling (a user can re-request twice if the
# first email got eaten by a spam filter) without giving an attacker
# a useful spam vector against a known email.
PASSWORD_RESET_REQUESTS_PER_HOUR = 3
PASSWORD_RESET_RATE_WINDOW = timedelta(hours=1)


# Constant DSGVO-compliant 200-OK message. Identical wording for
# success / unknown email / rate-limited cases — the response must
# not betray account existence to a probing attacker. (The audit
# log differentiates them; the user-facing surface does not.)
_PASSWORD_RESET_GENERIC_MSG = (
    "Falls ein Konto mit dieser E-Mail existiert, wurde eine E-Mail "
    "mit Anweisungen zum Zurücksetzen des Passworts gesendet."
)


@router.post("/password-reset")
async def request_password_reset(
    data: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Initiate the password-reset flow.

    Always returns 200 OK with the same German "if an account
    exists…" message — regardless of whether the email matches a
    real user, whether the user is rate-limited, or whether the
    Resend send actually succeeded. This is deliberate: a probing
    attacker must not be able to enumerate accounts via timing or
    response-shape differences.

    Behind the constant response, four branches:

    1. Email matches no user → audit row with ``user_id=None`` and
       ``meta.email`` for forensic context. No token, no email.
    2. Email matches a user but they've already requested 3 resets
       in the last hour → audit row with ``meta.rate_limited=True``,
       no new token, no email.
    3. Match + within budget → mint a single-use token (1h TTL),
       write the audit row, fire the Resend email. We swallow
       Resend failures: the audit row records the *attempt*, the
       operator notices via the WARN log line in
       ``app.services.email`` if the API actually 4xx'd.
    4. Unexpected exception → log + propagate 500 so we don't
       silently lose a user-facing error. Rare; the endpoint is
       deliberately defensive.
    """
    # Case-insensitive email lookup. ``user.email`` is stored
    # lowercased at registration, but a paranoid client typing
    # ``Tobi@Baulv.At`` should still hit the row.
    normalised_email = data.email.lower().strip()
    if not normalised_email:
        # Empty input. Still 200-OK to avoid leaking via shape, but
        # short-circuit before any DB work.
        return {"message": _PASSWORD_RESET_GENERIC_MSG}

    result = await db.execute(
        select(User).where(User.email == normalised_email)
    )
    user = result.scalars().first()

    if user is None:
        # No account. Audit the *attempt* anyway — repeated probes
        # show up in the operator's audit-grep as a flat sequence
        # of ``user_id=None`` reset_requested events.
        await log_event(
            db,
            event_type=EVENT_PASSWORD_RESET_REQUESTED,
            user_id=None,
            request=request,
            meta={"email": normalised_email, "result": "no_account"},
        )
        return {"message": _PASSWORD_RESET_GENERIC_MSG}

    # Rate-limit: count tokens minted for this user in the last hour.
    # Cheap — the index on ``user_id`` makes it a small range scan.
    window_start = datetime.now(timezone.utc) - PASSWORD_RESET_RATE_WINDOW
    count_result = await db.execute(
        select(func.count(PasswordResetToken.id)).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.created_at >= window_start,
        )
    )
    recent_count = count_result.scalar() or 0
    if recent_count >= PASSWORD_RESET_REQUESTS_PER_HOUR:
        await log_event(
            db,
            event_type=EVENT_PASSWORD_RESET_REQUESTED,
            user_id=user.id,
            request=request,
            meta={
                "email": normalised_email,
                "result": "rate_limited",
                "recent_count": recent_count,
            },
        )
        return {"message": _PASSWORD_RESET_GENERIC_MSG}

    # Mint + email. ``mint_reset_token`` invalidates any previous
    # outstanding tokens for this user, so the most-recent email
    # always wins.
    plaintext = await mint_reset_token(db, user=user)
    await log_event(
        db,
        event_type=EVENT_PASSWORD_RESET_REQUESTED,
        user_id=user.id,
        request=request,
        meta={"email": normalised_email, "result": "sent"},
    )
    # Email send is fire-and-forget from the user's perspective —
    # the response is the same whether or not Resend's API call
    # succeeded. The service logs success/failure on its own.
    send_password_reset_email(
        to_email=normalised_email,
        reset_token=plaintext,
        user_name=user.full_name or None,
    )
    return {"message": _PASSWORD_RESET_GENERIC_MSG}


@router.post("/password-reset/confirm", status_code=200)
async def confirm_password_reset(
    data: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Redeem a reset token and set a new password.

    Three failure modes collapse into the same generic 400:
    unknown / used / expired token. We do *not* differentiate —
    a precise error ("token expired") would let an attacker
    distinguish "valid but stale" from "never existed", which is
    a probing oracle.

    On success:

    1. The token row's ``used_at`` flips to ``now()`` (single-use
       guarantee — a second redeem on the same token 400s).
    2. The user's password hash gets rewritten via bcrypt.
    3. Every other session for this user is revoked. Mirrors the
       behaviour of ``POST /me/password`` — a successful reset is
       Treat as "I lost control of my account" and we kick all
       devices off. The user has to log in fresh after redeeming.
    4. An audit row records the completed reset with the user's id.
    """
    # Minimum length applies the same way as the change-password
    # path. We deliberately don't share the constant with
    # ``change_password`` because that endpoint may grow stricter
    # rules independently in future.
    if len(data.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Neues Passwort muss mindestens 8 Zeichen lang sein.",
        )

    verified = await verify_reset_token(db, presented_token=data.token)
    if verified is None:
        # Generic message — see docstring for why we don't
        # differentiate the failure modes.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Der Link ist ungültig oder abgelaufen. Bitte fordern "
                "Sie eine neue E-Mail an."
            ),
        )

    token_row, user = verified

    # Mark the token used inside the same transaction as the
    # password write. If the bcrypt step throws (extremely rare —
    # OOM territory), the token stays valid because the rollback
    # un-marks it.
    await mark_token_used(db, token_id=token_row.id)
    user.password_hash = hash_password(data.new_password)

    # Revoke every live session for this user. No "current session"
    # to preserve here — the reset flow is unauthenticated; the user
    # has to log in fresh after success.
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.flush()

    await log_event(
        db,
        event_type=EVENT_PASSWORD_RESET_COMPLETED,
        user_id=user.id,
        request=request,
    )
    return {
        "message": (
            "Passwort wurde erfolgreich zurückgesetzt. Sie können sich "
            "jetzt mit Ihrem neuen Passwort anmelden."
        )
    }


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
    marketing_changed = False
    if data.marketing_email_opt_in is not None:
        if user.marketing_email_opt_in != data.marketing_email_opt_in:
            changes["marketing_email_opt_in"] = data.marketing_email_opt_in
            marketing_changed = True
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

    # DSGVO Art. 7 evidence — write a consent snapshot whenever the
    # marketing flag actually flips. We don't write one for no-op
    # PUTs (idempotent calls from the frontend) so the table doesn't
    # accumulate noise.
    if marketing_changed:
        await record_marketing_optin_change(
            db,
            user_id=user.id,
            new_value=user.marketing_email_opt_in,
            request=request,
        )

    return _user_response(user)


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
