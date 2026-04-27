"""add api_keys table for headless agent credentials (PATs)

Revision ID: 014
Revises: 013
Create Date: 2026-04-27

Backs the v18.1 "MCP server" work (step 3a). Headless principals
(Claude Desktop, n8n, cron jobs) can't sensibly use the JWT-based
``user_sessions`` flow — see the model docstring in
``app/db/models/api_key.py`` for the full reasoning.

Migration is purely additive: new table, two indexes (``key_prefix``
for verification lookup, ``user_id`` for the user's "my API keys"
list). No existing schema is touched, no data migration is needed.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "api_keys" in inspector.get_table_names():
        # Idempotent re-run guard. The table is brand new in this
        # release, but a downgrade-then-upgrade in the dev DB shouldn't
        # explode.
        return

    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_prefix", sa.String(20), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    # Prefix index — verification path queries by prefix first to narrow
    # down to a single (or near-single) row before SHA-256 compare.
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_keys" not in inspector.get_table_names():
        return
    for idx in ("ix_api_keys_key_prefix", "ix_api_keys_user_id"):
        try:
            op.drop_index(idx, table_name="api_keys")
        except Exception:
            pass
    op.drop_table("api_keys")
