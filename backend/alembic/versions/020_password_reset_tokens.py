"""DS-3: password_reset_tokens table for the functional reset flow

Revision ID: 020
Revises: 019
Create Date: 2026-05-04

DS-3 — close the password-reset gap (v23.4).

Background
----------

Until v23.4 the ``POST /api/auth/password-reset`` endpoint was a
hard-coded stub that returned the German "if such an account
exists…" message without ever generating a token or sending an
email. That meant a user who actually forgot their password had no
working recovery path — the only fix was a manual SQL update
against the ``users`` table, which doesn't scale beyond the closed
beta and which violates Art. 32 DSGVO (security of processing —
users must be able to regain control of their account).

This migration introduces the storage layer for the functional flow:

* One row per reset request, indexed by the SHA-256 hash of the
  random URL-safe token (256 bits of entropy, single-use).
* ``expires_at`` is the 1-hour validity window, set by the
  application layer when the row is inserted.
* ``used_at`` flips from NULL to ``now()`` exactly once when the
  token is redeemed; a uniqueness invariant on ``token_hash`` plus
  the application's atomic UPDATE-WHERE-NULL guarantees no token
  can be redeemed twice.
* CASCADE on ``user_id`` — Art. 17 erasure must take the reset
  history with it.

Index strategy
--------------

* ``token_hash`` is unique + indexed: the redeem path looks the row
  up by hash on every ``POST /password-reset/confirm``. No other
  access pattern needs an index.
* ``user_id`` is indexed: the request path invalidates any
  previously-issued tokens for the user before issuing a new one
  (so the most recent email always wins). That's a small cardinality
  per user, but the index keeps the lookup constant-time as we grow.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "password_reset_tokens" not in inspector.get_table_names():
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                PG_UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "token_hash",
                sa.String(length=64),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "used_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        # The redeem path's primary lookup is by hash. ``unique=True``
        # already creates an index on Postgres, but we name it
        # explicitly so downgrade can drop it deterministically.
        op.create_index(
            "ix_password_reset_tokens_token_hash",
            "password_reset_tokens",
            ["token_hash"],
            unique=True,
        )
        # Used by the request path to invalidate any prior tokens for
        # the same user before issuing a new one.
        op.create_index(
            "ix_password_reset_tokens_user_id",
            "password_reset_tokens",
            ["user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "password_reset_tokens" in inspector.get_table_names():
        op.drop_index(
            "ix_password_reset_tokens_user_id",
            table_name="password_reset_tokens",
        )
        op.drop_index(
            "ix_password_reset_tokens_token_hash",
            table_name="password_reset_tokens",
        )
        op.drop_table("password_reset_tokens")
