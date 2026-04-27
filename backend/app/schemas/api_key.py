"""Pydantic schemas for the API-key (PAT) endpoints.

The plaintext token is in ``ApiKeyCreated`` and **only** there. Every
list/get response carries the prefix and metadata, never the secret —
the user has exactly one chance to copy it after creation, mirroring
the GitHub / Anthropic / Stripe convention.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    """Request body for ``POST /api/auth/me/api-keys``."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description=(
            "Human-recognisable label, e.g. 'Claude Desktop' or "
            "'n8n production'. Free text — used only for display."
        ),
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,  # ~10 years — anything past that, just don't expire
        description=(
            "Optional self-destruct window in days from now. NULL "
            "(default) = never expires. Once set, ``verify_pat`` "
            "rejects the credential after this many days, the same "
            "way revocation does. Capped at 3650 days as a sanity "
            "guard — beyond that, just don't set an expiry."
        ),
    )


class ApiKeyUpdate(BaseModel):
    """Request body for ``PATCH /api/auth/me/api-keys/{id}``.

    Currently the only mutable field is ``expires_in_days``. Setting
    it to ``None`` clears the expiry (key becomes "never expires");
    a positive integer pushes the expiry to N days from now.
    Renaming a key is intentionally not supported — the name is the
    "what is this for" comment the user wrote at creation time, and
    rewriting history confuses the audit log.
    """

    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description=(
            "New expiry window in days from now. ``None`` clears "
            "any existing expiry."
        ),
    )
    clear_expires: bool = Field(
        default=False,
        description=(
            "If true, ignore ``expires_in_days`` and set ``expires_at`` "
            "to NULL. Two-field shape because Pydantic doesn't have a "
            "clean way to distinguish 'omit field' from 'set to null' "
            "in a JSON body."
        ),
    )


class ApiKeyResponse(BaseModel):
    """List/detail response — never carries the plaintext token."""

    id: UUID
    name: str
    key_prefix: str = Field(
        ...,
        description=(
            "Visible portion of the token (e.g. ``pat_abc123``). "
            "Surfaced so the user can recognise which key they're "
            "looking at without revealing the full secret."
        ),
    )
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyResponse):
    """Response from ``POST`` — the **only** place the plaintext lives.

    The frontend is expected to display the ``token`` to the user
    immediately and warn that it won't be shown again. After this
    response the server has discarded the plaintext and only the
    SHA-256 hash remains.
    """

    token: str = Field(
        ...,
        description=(
            "Plaintext token — shown ONCE at creation time, never "
            "retrievable later. Use as ``Authorization: Bearer <token>`` "
            "against the ``/mcp`` endpoint."
        ),
    )


class McpAuditEntryResponse(BaseModel):
    """One row in the per-key audit log feed."""

    id: UUID
    api_key_id: UUID | None
    tool_name: str
    arguments: dict | None
    result: str = Field(
        ...,
        description=(
            "Outcome tag: ``ok`` | ``error`` | ``rate_limited``. "
            "Promoted from a free-text column to a discrete vocabulary "
            "for the frontend filter."
        ),
    )
    error_message: str | None
    latency_ms: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedMcpAuditResponse(BaseModel):
    """Cursor-less paginated response for the audit-log feed.

    We use offset+limit (not opaque cursor) because the underlying
    composite index ``(user_id, created_at DESC)`` makes offset cheap
    *and* the frontend wants random-access pagination ("page 5 of 12")
    that cursors don't trivially deliver. The ``total`` count is exact;
    rows are immutable so the count doesn't drift mid-pagination.
    """

    items: list[McpAuditEntryResponse]
    total: int = Field(
        ...,
        description="Total number of rows matching the query (exact).",
    )
    limit: int
    offset: int
