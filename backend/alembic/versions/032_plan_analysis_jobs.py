"""Hintergrund-Jobs für die Plananalyse — plan_analysis_jobs (2026-11)

Revision ID: 032
Revises: 031
Create Date: 2026-11-23

Die Analyse läuft nicht mehr in der HTTP-Anfrage (Cloudflare-524 nach
~100 s), sondern als Job im App-Prozess. Diese Tabelle hält Status,
Fortschritt, Heartbeat und Ergebnis. Downgrade droppt die Tabelle (nur
Laufzeit-Status, keine Nutzdaten — die Räume liegen weiter in rooms).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "plan_analysis_jobs"
_INDEX = "ix_plan_analysis_jobs_plan_id"


def upgrade() -> None:
    # Idempotent wie 028: läuft zwar unter dem Advisory-Lock aus
    # app.main._run_migrations_with_lock, soll aber auch einen
    # ungesperrten oder wiederholten Lauf (Lock-Fallback, halb
    # abgebrochener Deploy) ohne DuplicateTable überstehen.
    bind = op.get_bind()

    def _has_table() -> bool:
        return sa.inspect(bind).has_table(_TABLE)

    def _has_index() -> bool:
        return any(
            ix["name"] == _INDEX for ix in sa.inspect(bind).get_indexes(_TABLE)
        )

    if _has_table():
        if not _has_index():
            op.create_index(_INDEX, _TABLE, ["plan_id"])
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.Uuid(),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("pages_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_total", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("error_status", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(_INDEX, _TABLE, ["plan_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)  # nimmt den Index mit
