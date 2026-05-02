"""rooms: pixel coordinates + page number for plan-pin display

Revision ID: 018
Revises: 017
Create Date: 2026-05-01

Phase 1 of the plan-visualisation feature: data foundation only.
We add five nullable integer columns to ``rooms`` so the Vision
pipeline can persist where each room sits on the rendered plan
image. Phase 2 (the actual pin-on-plan UI) will read these in a
separate change; this migration only adds the columns and leaves
all existing rows with NULL coordinates — no UI consequence yet.

Why nullable + integer
----------------------

* ``NULL`` is the honest "Vision wasn't sure" / "this room came in
  via the manual editor and has no plan placement" answer. Phase 2
  shows pins only for rooms with non-null coords; the others stay
  in the regular table.
* Integers in pixel space match the 300 DPI PNG render the pipeline
  ships to Vision. We don't normalise to 0..1 here because the
  user spec asked for pixel coordinates explicitly; if Phase 2
  needs render dimensions to scale, a later migration can add a
  ``plans.rendered_width_px`` / ``rendered_height_px`` pair.

The five fields:
  * ``position_x`` / ``position_y``  — center of the room label
                                        (or its visual centroid)
  * ``page_number``                  — 1-based PDF page index
  * ``bbox_width`` / ``bbox_height`` — Vision's estimate of the
                                        room's apparent size on the
                                        plan, used by Phase 2 to
                                        scale pins proportionally
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Names of the columns this migration manages. Defined once so the
# add and drop loops below stay in lock-step.
_COLUMNS = (
    "position_x",
    "position_y",
    "page_number",
    "bbox_width",
    "bbox_height",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("rooms")}

    for name in _COLUMNS:
        if name not in existing:
            op.add_column(
                "rooms",
                sa.Column(name, sa.Integer(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("rooms")}

    for name in _COLUMNS:
        if name in existing:
            op.drop_column("rooms", name)
