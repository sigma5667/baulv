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

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_key_auth import (
    display_prefix,
    hash_token,
    mint_token,
)
from app.auth import get_current_user
from app.db.models.api_key import ApiKey
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyResponse
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

    token = mint_token()
    api_key = ApiKey(
        user_id=user.id,
        name=payload.name.strip(),
        key_prefix=display_prefix(token),
        key_hash=hash_token(token),
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    await log_event(
        db,
        event_type=EVENT_API_KEY_CREATED,
        user_id=user.id,
        request=request,
        meta={"key_id": str(api_key.id), "name": api_key.name},
    )
    await db.commit()

    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
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
