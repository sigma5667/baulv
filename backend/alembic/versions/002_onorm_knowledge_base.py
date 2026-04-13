"""ÖNORM knowledge base: dokumente, chunks, regeln

Revision ID: 002
Revises: 001
Create Date: 2026-03-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "onorm_dokumente",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("norm_nummer", sa.String(50), nullable=False),
        sa.Column("titel", sa.String(500)),
        sa.Column("ausgabe_datum", sa.Date()),
        sa.Column("file_path", sa.String(1000)),
        sa.Column("upload_status", sa.String(50), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "onorm_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("dokument_id", sa.Uuid(), sa.ForeignKey("onorm_dokumente.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("section_number", sa.String(50)),
        sa.Column("section_title", sa.String(255)),
        sa.Column("page_number", sa.Integer()),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )

    op.create_table(
        "onorm_regeln",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("dokument_id", sa.Uuid(), sa.ForeignKey("onorm_dokumente.id", ondelete="SET NULL")),
        sa.Column("regel_code", sa.String(100), nullable=False, unique=True),
        sa.Column("trade", sa.String(100), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("description_de", sa.Text(), nullable=False),
        sa.Column("formula_type", sa.String(50)),
        sa.Column("parameters", postgresql.JSONB(), server_default="{}"),
        sa.Column("onorm_reference", sa.String(100)),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("onorm_regeln")
    op.drop_table("onorm_chunks")
    op.drop_table("onorm_dokumente")
