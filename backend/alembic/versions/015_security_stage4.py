"""security stage 4: api_keys.expires_at + mcp_audit_log_entries

Revision ID: 015
Revises: 014
Create Date: 2026-04-27

Two changes, kept in one revision because they ship together with the
v19 security pass and a half-applied state would leave the runtime in
an inconsistent place:

1. ``api_keys.expires_at`` — optional self-destruct timestamp the user
   sets at creation time. NULL means "never expires" (the default), a
   value means ``verify_pat`` rejects the credential once that wall
   passes. Added as a nullable column with no default so existing rows
   keep their current "no expiry" semantics on upgrade.

2. ``mcp_audit_log_entries`` — append-only DSGVO Art. 32 trail of every
   MCP tool dispatch (tool name, sanitised args, ok/error, latency).
   Modelled after ``audit_log_entries`` but separate because the
   write rate and the read shape are very different — MCP audit is
   high-volume and queried per-user paginated, whereas the canonical
   audit log is low-volume and surfaced as part of account events.

Both indexes on ``mcp_audit_log_entries`` are descending on
``created_at`` so the "show me the last N tool calls" query path —
which is what the frontend viewer issues — runs straight off the
index without a sort.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. api_keys.expires_at -------------------------------------
    api_key_columns = {c["name"] for c in inspector.get_columns("api_keys")}
    if "expires_at" not in api_key_columns:
        op.add_column(
            "api_keys",
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )

    # --- 2. mcp_audit_log_entries -----------------------------------
    if "mcp_audit_log_entries" not in inspector.get_table_names():
        op.create_table(
            "mcp_audit_log_entries",
            sa.Column(
                "id",
                sa.Uuid(),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            # SET NULL (not CASCADE): account-deletion must not erase
            # the trail. Same reasoning as ``audit_log_entries``.
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            # Same SET NULL trick — revoking + deleting a key shouldn't
            # rip its history out of the table.
            sa.Column(
                "api_key_id",
                sa.Uuid(),
                sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("tool_name", sa.String(64), nullable=False),
            # Sanitised arguments — large blobs (file contents, full
            # PDFs) are clipped before insertion. JSONB so the frontend
            # viewer can render structured deltas.
            sa.Column(
                "arguments",
                sa.dialects.postgresql.JSONB(),
                nullable=True,
            ),
            # ``ok`` | ``error`` — short enum-ish string. Keeping it
            # text rather than a real enum so adding new outcomes (e.g.
            # ``rate_limited``) doesn't need a migration.
            sa.Column("result", sa.String(16), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            # Wall-clock latency in ms, captured around the dispatcher
            # call. Useful for the user to spot "this tool is slow on
            # my data" without us shipping a separate metrics surface.
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        # Per-user reverse-chronological scan — the audit viewer's main
        # query. Composite + DESC so pagination is a forward index seek.
        op.create_index(
            "ix_mcp_audit_user_created",
            "mcp_audit_log_entries",
            ["user_id", sa.text("created_at DESC")],
        )
        # Per-key drill-down — "show me everything Claude Desktop did"
        # in the key detail view.
        op.create_index(
            "ix_mcp_audit_key_created",
            "mcp_audit_log_entries",
            ["api_key_id", sa.text("created_at DESC")],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "mcp_audit_log_entries" in inspector.get_table_names():
        for idx in (
            "ix_mcp_audit_key_created",
            "ix_mcp_audit_user_created",
        ):
            try:
                op.drop_index(idx, table_name="mcp_audit_log_entries")
            except Exception:
                pass
        op.drop_table("mcp_audit_log_entries")

    api_key_columns = {c["name"] for c in inspector.get_columns("api_keys")}
    if "expires_at" in api_key_columns:
        op.drop_column("api_keys", "expires_at")
