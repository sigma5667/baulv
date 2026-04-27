"""HTTP endpoints for managing the user's PATs.

Mounted under ``/api/auth/me/api-keys`` — same logical home as the
other "stuff about the current user" endpoints, but a separate router
so the auth surface stays inspectable in isolation.

Auth model
==========

These endpoints accept **only JWT** (via ``get_current_user``), not
PAT. Letting an agent rotate its own credentials is a meaningful
escalation surface — if a PAT leaks, the attacker should not be able
to mint a fresh one to outlast the user's revocation. Same pattern as
GitHub: PAT-management endpoints require a user-interactive token.

Plaintext exposure
==================

The plaintext token is returned **once**, only by ``POST``, and never
persisted. ``GET`` responses carry only the prefix and metadata. Lost
your token? Mint a new one and revoke the old; there is no recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_key_auth import (
    display_prefix,
    hash_token,
    mint_token,
)
from app.auth import get_current_user
from app.db.models.api_key import ApiKey
from app.db.models.mcp_audit import McpAuditLogEntry
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyResponse,
    ApiKeyUpdate,
    McpAuditEntryResponse,
    PaginatedMcpAuditResponse,
)
from app.services.audit import log_event


router = APIRouter()


# Per-user soft cap. Not a security control — a sloppiness control.
# A user accumulating dozens of PATs in their account is almost
# certainly forgotten ones that should have been revoked instead.
MAX_KEYS_PER_USER = 20


# Audit event types specific to PAT lifecycle. Kept here rather than in
# ``services/audit.py`` because they're scoped to this surface — adding
# them to the canonical list would imply they're part of the broader
# user-event taxonomy when they're really feature-local.
EVENT_API_KEY_CREATED = "user.api_key_created"
EVENT_API_KEY_REVOKED = "user.api_key_revoked"
EVENT_API_KEY_UPDATED = "user.api_key_updated"


# Pagination cap on the audit feed. The viewer is for human eyeballs,
# and 100 rows already overflow most laptop screens. Higher values
# would also force ``COUNT(*)`` to scan further when the user filters.
_AUDIT_PAGE_MAX = 100


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new programmatic-access token",
    description=(
        "Mints a fresh PAT for the authenticated user. The plaintext "
        "token is returned in the response **once** and is not "
        "retrievable afterwards. Use it as `Authorization: Bearer "
        "pat_...` against the `/mcp` endpoint."
    ),
)
async def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    # Count only **active** keys against the cap. Soft-deleted (revoked)
    # rows are kept around for the audit trail, but they don't take up
    # a slot.
    active_count = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == user.id,
                ApiKey.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    if len(active_count) >= MAX_KEYS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Limit erreicht: maximal {MAX_KEYS_PER_USER} aktive "
                "API-Keys pro User. Bitte einen alten widerrufen."
            ),
        )

    expires_at: datetime | None = None
    if payload.expires_in_days is not None:
        # Compute against UTC now — the column is timezone-aware. The
        # window is interpreted as "elapsed wall-clock days from now",
        # which is what users expect when they pick "30 days".
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=payload.expires_in_days
        )

    token = mint_token()
    api_key = ApiKey(
        user_id=user.id,
        name=payload.name.strip(),
        key_prefix=display_prefix(token),
        key_hash=hash_token(token),
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    await log_event(
        db,
        event_type=EVENT_API_KEY_CREATED,
        user_id=user.id,
        request=request,
        meta={
            "key_id": str(api_key.id),
            "name": api_key.name,
            "expires_in_days": payload.expires_in_days,
        },
    )
    await db.commit()

    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        revoked_at=api_key.revoked_at,
        token=token,
    )


@router.get(
    "",
    response_model=list[ApiKeyResponse],
    summary="List my programmatic-access tokens",
    description=(
        "Returns every PAT belonging to the authenticated user, "
        "including revoked ones (so the audit trail stays complete). "
        "Plaintext tokens are never included."
    ),
)
async def list_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyResponse]:
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [ApiKeyResponse.model_validate(k) for k in keys]


@router.patch(
    "/{key_id}",
    response_model=ApiKeyResponse,
    summary="Update a programmatic-access token's expiry",
    description=(
        "Currently the only mutable field is the expiry window. "
        "Pass ``expires_in_days`` to push the expiry to N days from "
        "now, or ``clear_expires=true`` to mark the key as never-"
        "expiring. Renaming is not supported — the original name is "
        "treated as immutable history."
    ),
)
async def update_api_key(
    key_id: UUID,
    payload: ApiKeyUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyResponse:
    api_key = await db.get(ApiKey, key_id)
    if api_key is None or api_key.user_id != user.id:
        # 404-mask cross-user access to avoid leaking existence.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API-Key nicht gefunden.",
        )
    if api_key.revoked_at is not None:
        # Mutating a revoked key is a no-op semantically — but we
        # surface a clear 409 rather than silently succeeding so the
        # UI can show an actionable error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Widerrufene Keys können nicht mehr geändert werden.",
        )

    if payload.clear_expires:
        api_key.expires_at = None
    elif payload.expires_in_days is not None:
        api_key.expires_at = datetime.now(timezone.utc) + timedelta(
            days=payload.expires_in_days
        )
    else:
        # Neither field set — body was effectively empty. Don't error;
        # treat as a no-op so the frontend can issue a "save" click
        # without first reading the form.
        pass

    await db.flush()
    await db.refresh(api_key)
    await log_event(
        db,
        event_type=EVENT_API_KEY_UPDATED,
        user_id=user.id,
        request=request,
        meta={
            "key_id": str(api_key.id),
            "expires_at": api_key.expires_at.isoformat()
            if api_key.expires_at
            else None,
        },
    )
    await db.commit()
    return ApiKeyResponse.model_validate(api_key)


@router.get(
    "/{key_id}/audit",
    response_model=PaginatedMcpAuditResponse,
    summary="Browse this key's MCP audit log",
    description=(
        "Returns the rows ``app.mcp.server`` wrote for this key, "
        "newest first, paginated. Revoked keys still expose their "
        "history. Cross-user access is 404-masked."
    ),
)
async def get_api_key_audit(
    key_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=_AUDIT_PAGE_MAX),
    offset: int = Query(0, ge=0),
) -> PaginatedMcpAuditResponse:
    api_key = await db.get(ApiKey, key_id)
    if api_key is None or api_key.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API-Key nicht gefunden.",
        )

    # Total row count via a separate ``COUNT(*)`` — ``window``-style
    # ``count(*) OVER ()`` would join the count to every row but make
    # the query plan less obvious. The composite index makes both
    # queries cheap even at thousands of rows.
    total = (
        await db.execute(
            select(func.count(McpAuditLogEntry.id)).where(
                McpAuditLogEntry.api_key_id == key_id
            )
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(McpAuditLogEntry)
            .where(McpAuditLogEntry.api_key_id == key_id)
            .order_by(McpAuditLogEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return PaginatedMcpAuditResponse(
        items=[McpAuditEntryResponse.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a programmatic-access token",
    description=(
        "Marks the token as revoked. Soft delete — the row remains "
        "for audit purposes, but the next request presenting this "
        "token will be rejected. Idempotent: revoking an already-"
        "revoked key is a no-op 204."
    ),
)
async def revoke_api_key(
    key_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    api_key = await db.get(ApiKey, key_id)
    if api_key is None or api_key.user_id != user.id:
        # 404-mask cross-user access to avoid leaking existence.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API-Key nicht gefunden.",
        )

    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(timezone.utc)
        await log_event(
            db,
            event_type=EVENT_API_KEY_REVOKED,
            user_id=user.id,
            request=request,
            meta={"key_id": str(api_key.id), "name": api_key.name},
        )
        await db.commit()
    return None
