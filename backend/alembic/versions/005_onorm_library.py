"""ÖNORM library: add trade to dokumente + LV-ÖNORM selection table

Revision ID: 005
Revises: 004
Create Date: 2026-03-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add trade column to onorm_dokumente
    op.add_column("onorm_dokumente", sa.Column("trade", sa.String(100), nullable=True))

    # Create LV-ÖNORM many-to-many selection table
    op.create_table(
        "lv_onorm_selection",
        sa.Column("lv_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("leistungsverzeichnisse.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("onorm_dokument_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("onorm_dokumente.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("lv_onorm_selection")
    op.drop_column("onorm_dokumente", "trade")
