"""drop ÖNORM library tables and LV.onorm_basis column

Revision ID: 010
Revises: 009
Create Date: 2026-04-17

BauLV no longer presents itself as an ÖNORM library or cross-reference
tool. The user never selects or browses specific ÖNORM standards — the
calculation engine's rules are invoked by trade, and that's the only
norm-adjacent thing a user interacts with.

This migration drops everything in the DB that backed the old library:

* ``lv_onorm_selection`` — junction table for the per-LV ÖNORM picker UI
* ``onorm_regeln``       — the registry of norm-scoped calculation rules
                            (the rules themselves live in Python code
                            under ``app/calculation_engine/trades/``)
* ``onorm_dokumente``    — the metadata registry for uploaded ÖNORM PDFs
                            (PDF storage was already removed in 008)
* ``leistungsverzeichnisse.onorm_basis`` — per-LV free-text tag; was
                            previously shown as the "ÖNORM" field on the
                            LV edit form

What **stays**:

* ``berechnungsnachweise.onorm_factor``, ``.onorm_rule_ref``,
  ``.onorm_paragraph`` — these are math metadata the calculation
  engine emits alongside every measurement line for traceability. They
  never appear in the UI under that name (frontend renames them to
  generic terms) but we keep the DB columns so historical rows stay
  intact and the calculation engine can keep writing them without a
  simultaneous code+data migration.

Downgrade recreates the skeleton tables and column so a rollback
doesn't break the prior revision's imports, but can't restore any of
the data — by design, the data wasn't valuable (user-picked pointers,
not user content).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Junction table first — it has FKs into both sides.
    op.execute("DROP TABLE IF EXISTS lv_onorm_selection CASCADE")
    # Dependent table next.
    op.execute("DROP TABLE IF EXISTS onorm_regeln CASCADE")
    # Parent table last.
    op.execute("DROP TABLE IF EXISTS onorm_dokumente CASCADE")

    # Drop the free-text tag column from leistungsverzeichnisse. Wrapped
    # in a conditional check so re-running on an already-migrated DB
    # stays a no-op (belt-and-braces for manual remediation scenarios).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("leistungsverzeichnisse")}
    if "onorm_basis" in cols:
        op.drop_column("leistungsverzeichnisse", "onorm_basis")


def downgrade() -> None:
    # Restore onorm_basis column (nullable; no data to restore).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("leistungsverzeichnisse")}
    if "onorm_basis" not in cols:
        op.add_column(
            "leistungsverzeichnisse",
            sa.Column("onorm_basis", sa.String(length=100), nullable=True),
        )

    # Recreate the registry tables. Shapes mirror 002 + 005 + 008 post-
    # state. No FK to leistungsverzeichnisse from onorm_dokumente.
    op.create_table(
        "onorm_dokumente",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("norm_nummer", sa.String(length=50), nullable=False),
        sa.Column("titel", sa.String(length=500), nullable=True),
        sa.Column("trade", sa.String(length=100), nullable=True),
        sa.Column("ausgabe_datum", sa.Date(), nullable=True),
        sa.Column(
            "upload_status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "onorm_regeln",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "dokument_id",
            UUID(as_uuid=True),
            sa.ForeignKey("onorm_dokumente.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("regel_code", sa.String(length=100), nullable=False, unique=True),
        sa.Column("trade", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("description_de", sa.Text(), nullable=False),
        sa.Column("formula_type", sa.String(length=50), nullable=True),
        sa.Column("parameters", JSONB(), nullable=False, server_default="{}"),
        sa.Column("onorm_reference", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "lv_onorm_selection",
        sa.Column(
            "lv_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leistungsverzeichnisse.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "onorm_dokument_id",
            UUID(as_uuid=True),
            sa.ForeignKey("onorm_dokumente.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
