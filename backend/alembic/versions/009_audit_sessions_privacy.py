"""audit log, user sessions, and privacy settings

Revision ID: 009
Revises: 008
Create Date: 2026-04-15

This migration adds the schema changes that back three DSGVO compliance
features added in the same release:

* ``audit_log_entries`` — append-only record of sensitive account events
  (login, logout, password change, export, delete). Required under
  Art. 32 DSGVO (security of processing) and useful for the user's own
  review. ``user_id`` is ``ON DELETE SET NULL`` so the log survives
  account deletion — the user is gone but their historical events
  remain for the controller's records, without any personally
  identifying join target.

* ``user_sessions`` — per-token row used to make JWTs revocable. Each
  issued token carries a ``jti`` (JWT ID) that matches a row here, so
  we can invalidate individual sessions (or all but the current one on
  password change) without rotating the JWT signing key. Cascade
  deletes with the user row.

* ``users.marketing_email_opt_in`` — explicit consent flag for the
  optional marketing email channel. Defaults to ``false`` so existing
  users are treated as not having opted in (DSGVO requires opt-in, not
  opt-out, for marketing).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- audit log ------------------------------------------------------
    op.create_table(
        "audit_log_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        # Freeform metadata per event type. Kept deliberately loose so we
        # can evolve the event schema without migrations.
        sa.Column("meta", JSONB, nullable=True),
        # INET is native in Postgres and keeps both IPv4 and IPv6 cleanly.
        sa.Column("ip_address", INET, nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
    )

    # --- user sessions --------------------------------------------------
    op.create_table(
        "user_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # The JWT ``jti`` claim. Unique so we can look up sessions by
        # the token presented on each request.
        sa.Column("jti", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("ip_address", INET, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Soft-revocation: set this and the session fails auth without
        # being deleted, so it still shows up in the user's audit trail
        # until expires_at passes.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- privacy flag ---------------------------------------------------
    op.add_column(
        "users",
        sa.Column(
            "marketing_email_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "marketing_email_opt_in")
    op.drop_table("user_sessions")
    op.drop_table("audit_log_entries")
