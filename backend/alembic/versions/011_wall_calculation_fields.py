"""add wall-calculation fields to rooms

Revision ID: 011
Revises: 010
Create Date: 2026-04-22

Backs the "Automatische Wandflächen- und Umfangberechnung" feature.
The AI room extraction already produces a wall perimeter and ceiling
height per room; this migration adds the columns the calculation
service writes back after applying Austrian building conventions
(stairwell factor, height surcharge, opening deductions).

New columns on ``rooms``:

* ``ceiling_height_source`` VARCHAR(20) NOT NULL DEFAULT 'default'
    — one of ``schnitt`` | ``grundriss`` | ``manual`` | ``default``.
    The frontend uses this to highlight rooms whose ceiling height
    was assumed so the user confirms before the number flows into
    the LV. Existing rooms keep the conservative ``default`` marker.

* ``wall_area_gross_m2`` NUMERIC(10,3) NULL
    — cached ``perimeter × height × applied_factor``.

* ``wall_area_net_m2`` NUMERIC(10,3) NULL
    — gross minus opening areas (openings ≥ 2.5 m²) when
    ``deductions_enabled`` is true, otherwise equal to gross.

* ``applied_factor`` NUMERIC(4,3) NULL
    — which multiplier was used on the last calculation
    (1.000 normal, 1.120 for 3–4 m, 1.160 for >4 m, 1.500 stairwell).

* ``deductions_enabled`` BOOLEAN NOT NULL DEFAULT TRUE
    — per-room toggle. True = subtract large openings; False = treat
    gross as net (conservative override).

All values are nullable except the two flags, which have server-side
defaults so a pre-existing row satisfies the NOT NULL constraint
without an explicit backfill pass.
"""

from alembic import op
import sqlalchemy as sa


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("rooms")}

    if "ceiling_height_source" not in existing:
        op.add_column(
            "rooms",
            sa.Column(
                "ceiling_height_source",
                sa.String(length=20),
                nullable=False,
                server_default="default",
            ),
        )
    if "wall_area_gross_m2" not in existing:
        op.add_column(
            "rooms",
            sa.Column("wall_area_gross_m2", sa.Numeric(10, 3), nullable=True),
        )
    if "wall_area_net_m2" not in existing:
        op.add_column(
            "rooms",
            sa.Column("wall_area_net_m2", sa.Numeric(10, 3), nullable=True),
        )
    if "applied_factor" not in existing:
        op.add_column(
            "rooms",
            sa.Column("applied_factor", sa.Numeric(4, 3), nullable=True),
        )
    if "deductions_enabled" not in existing:
        op.add_column(
            "rooms",
            sa.Column(
                "deductions_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("rooms")}

    for col in (
        "deductions_enabled",
        "applied_factor",
        "wall_area_net_m2",
        "wall_area_gross_m2",
        "ceiling_height_source",
    ):
        if col in existing:
            op.drop_column("rooms", col)
