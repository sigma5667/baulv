"""Tageszähler für Kostenbremsen — daily_usage_counters (Audit 2026-11)

Revision ID: 031
Revises: 030
Create Date: 2026-11-02

Globaler Tagesdeckel für den öffentlichen Support-Chat und KI-
Tageslimit pro Konto. In der DB statt im Prozess-Speicher, damit der
Zähler Deploys übersteht. Downgrade droppt die Tabelle (nur Zählerstände,
keine Nutzdaten).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_usage_counters",
        sa.Column("counter_key", sa.String(64), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("daily_usage_counters")
