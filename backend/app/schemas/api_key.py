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
