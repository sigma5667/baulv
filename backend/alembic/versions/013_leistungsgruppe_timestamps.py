"""add created_at / updated_at to leistungsgruppen

Revision ID: 013
Revises: 012
Create Date: 2026-04-27

Side-quest of the v18 stable-IDs work. The ``Leistungsgruppe`` model
gained ``created_at`` and ``updated_at`` columns so callers — primarily
the upcoming MCP endpoints — can ask "when was this group first
surfaced for the LV?" and "when was it last touched?".

The columns mirror the ones already present on ``Position`` and
``Leistungsverzeichnis``, so the schema is now consistent across the
LV-tree levels.

Idempotency
-----------

The upgrade is guarded with a column-existence check so reruns on a DB
that has already been migrated are no-ops. Both columns are added with
``server_default = now()`` and ``NOT NULL`` so existing rows get
populated atomically — no two-step "add nullable, backfill, set NOT
NULL" dance is needed for a small table like this.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "leistungsgruppen" not in inspector.get_table_names():
        # Table not present yet (very early DB) — nothing to do; the
        # migration that creates it will already define the columns.
        return

    existing_cols = {c["name"] for c in inspector.get_columns("leistungsgruppen")}

    if "created_at" not in existing_cols:
        op.add_column(
            "leistungsgruppen",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

    if "updated_at" not in existing_cols:
        op.add_column(
            "leistungsgruppen",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "leistungsgruppen" not in inspector.get_table_names():
        return

    existing_cols = {c["name"] for c in inspector.get_columns("leistungsgruppen")}

    if "updated_at" in existing_cols:
        op.drop_column("leistungsgruppen", "updated_at")
    if "created_at" in existing_cols:
        op.drop_column("leistungsgruppen", "created_at")
