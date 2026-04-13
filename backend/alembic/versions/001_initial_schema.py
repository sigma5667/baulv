"""Initial schema: projects, buildings, floors, units, rooms, openings, plans

Revision ID: 001
Revises: None
Create Date: 2026-03-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("address", sa.Text()),
        sa.Column("client_name", sa.String(255)),
        sa.Column("project_number", sa.String(100)),
        sa.Column("grundstuecksnr", sa.String(100)),
        sa.Column("planverfasser", sa.String(255)),
        sa.Column("status", sa.String(50), server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger()),
        sa.Column("page_count", sa.Integer()),
        sa.Column("plan_type", sa.String(50)),
        sa.Column("analysis_status", sa.String(50), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "buildings",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "floors",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("building_id", sa.Uuid(), sa.ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("level_number", sa.Integer()),
        sa.Column("floor_height_m", sa.Numeric(6, 3)),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
    )

    op.create_table(
        "units",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("floor_id", sa.Uuid(), sa.ForeignKey("floors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("unit_type", sa.String(50)),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
    )

    op.create_table(
        "rooms",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", sa.Uuid(), sa.ForeignKey("units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("room_number", sa.String(50)),
        sa.Column("room_type", sa.String(100)),
        sa.Column("area_m2", sa.Numeric(10, 3)),
        sa.Column("perimeter_m", sa.Numeric(10, 3)),
        sa.Column("height_m", sa.Numeric(6, 3)),
        sa.Column("floor_type", sa.String(100)),
        sa.Column("wall_type", sa.String(100)),
        sa.Column("ceiling_type", sa.String(100)),
        sa.Column("is_wet_room", sa.Boolean(), server_default="false"),
        sa.Column("has_dachschraege", sa.Boolean(), server_default="false"),
        sa.Column("is_staircase", sa.Boolean(), server_default="false"),
        sa.Column("source", sa.String(50), server_default="manual"),
        sa.Column("ai_confidence", sa.Numeric(4, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "openings",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("room_id", sa.Uuid(), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opening_type", sa.String(50), nullable=False),
        sa.Column("width_m", sa.Numeric(6, 3), nullable=False),
        sa.Column("height_m", sa.Numeric(6, 3), nullable=False),
        sa.Column("count", sa.Integer(), server_default="1"),
        sa.Column("description", sa.String(255)),
        sa.Column("source", sa.String(50), server_default="manual"),
    )


def downgrade() -> None:
    op.drop_table("openings")
    op.drop_table("rooms")
    op.drop_table("units")
    op.drop_table("floors")
    op.drop_table("buildings")
    op.drop_table("plans")
    op.drop_table("projects")
