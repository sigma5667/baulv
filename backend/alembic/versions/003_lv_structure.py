"""LV structure: leistungsverzeichnisse, gruppen, positionen, berechnungsnachweise

Revision ID: 003
Revises: 002
Create Date: 2026-03-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leistungsverzeichnisse",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("trade", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), server_default="draft"),
        sa.Column("onorm_basis", sa.String(100)),
        sa.Column("vorbemerkungen", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "leistungsgruppen",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lv_id", sa.Uuid(), sa.ForeignKey("leistungsverzeichnisse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nummer", sa.String(20), nullable=False),
        sa.Column("bezeichnung", sa.String(500), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
    )

    op.create_table(
        "positionen",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("gruppe_id", sa.Uuid(), sa.ForeignKey("leistungsgruppen.id", ondelete="CASCADE"), nullable=False),
        sa.Column("positions_nummer", sa.String(20), nullable=False),
        sa.Column("kurztext", sa.String(500), nullable=False),
        sa.Column("langtext", sa.Text()),
        sa.Column("einheit", sa.String(20), nullable=False),
        sa.Column("menge", sa.Numeric(12, 3)),
        sa.Column("einheitspreis", sa.Numeric(12, 2)),
        sa.Column("positionsart", sa.String(50), server_default="normal"),
        sa.Column("text_source", sa.String(50), server_default="ai"),
        sa.Column("is_locked", sa.Boolean(), server_default="false"),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "berechnungsnachweise",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("position_id", sa.Uuid(), sa.ForeignKey("positionen.id", ondelete="CASCADE"), nullable=False),
        sa.Column("room_id", sa.Uuid(), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("formula_description", sa.Text(), nullable=False),
        sa.Column("formula_expression", sa.Text(), nullable=False),
        sa.Column("onorm_factor", sa.Numeric(8, 4), server_default="1.0"),
        sa.Column("onorm_rule_ref", sa.String(100)),
        sa.Column("onorm_paragraph", sa.String(100)),
        sa.Column("deductions", postgresql.JSONB(), server_default="[]"),
        sa.Column("net_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("berechnungsnachweise")
    op.drop_table("positionen")
    op.drop_table("leistungsgruppen")
    op.drop_table("leistungsverzeichnisse")
