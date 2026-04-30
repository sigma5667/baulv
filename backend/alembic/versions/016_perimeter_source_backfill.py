"""perimeter_source: track provenance + backfill estimates from area

Revision ID: 016
Revises: 015
Create Date: 2026-04-29

Adds ``rooms.perimeter_source`` and back-fills it for every existing
row, plus estimates a perimeter for rooms that have an area but no
perimeter (the common Vision-extraction failure mode for plain
floorplans without dimensioning).

Why this lives in a migration, not a one-off script
---------------------------------------------------

* The Railway lifespan runs ``alembic upgrade head`` on every boot
  (see ``app/main.py``), so a migration is the durable, automatic
  way to land the back-fill — no admin endpoint, no shell access
  required, no possibility of the operator forgetting to flip a
  switch after deploy.
* The back-fill is deterministic and bounded — it runs exactly once
  per environment (Alembic's revision book-keeping prevents re-runs).
* New rooms ingested after this revision go through the updated
  ``plan_analysis/pipeline.py``, which sets ``perimeter_source``
  explicitly. Existing rooms (test data, beta-tester real projects)
  need this one-shot fill so they don't keep showing 0,00 m² brutto
  in the wall-calc table.

What the four UPDATE blocks do
------------------------------

1. **Mark Vision-sourced perimeters**: any AI-imported room
   (``source = 'ai'``) with a non-null perimeter — that's a value
   the old pipeline passed straight from Vision. Tag it ``vision``
   so the new UI doesn't flag it as estimated.

2. **Mark manual perimeters**: any user-created room
   (``source = 'manual'``) with a non-null perimeter — the user typed
   it in. Tag it ``manual``.

3. **Estimate missing perimeters**: rooms with a known area but no
   perimeter. The estimator assumes a near-square footprint with a
   1.10 fudge factor for L-shapes and minor irregularities:

     P_est = 4 · sqrt(A) · 1.10

   For a 20 m² room that's 4 · 4.47 · 1.10 ≈ 19.67 m. That's an
   honest "good enough to start" rather than the previous null which
   produced 0,00 m² brutto and confused the user.

4. **Recompute the wall-area cache** for the rows we just touched —
   factor, gross, net — using the same Austrian rules the Python
   ``wall_calculator`` service applies on live writes
   (1.5 × for staircases, 1.16 / 1.12 for ceiling-height ladders,
   openings ≥ 2.5 m² deducted from net when ``deductions_enabled``).
   Encoded in PG-SQL because (a) it's bounded to the freshly-filled
   ``estimated`` rows and (b) duplicating one CASE-ladder is cleaner
   than wiring an async wall-calc invocation into a sync migration.
   The migration is a snapshot — future formula changes don't need
   to retroactively re-fill old rows.

Rooms with neither perimeter nor area — the genuine unknowns — stay
``perimeter_m IS NULL AND perimeter_source IS NULL``. The frontend
keeps showing them as the red "Bitte eintragen" emergency-fallback
badge so they're impossible to overlook.

Downgrade drops the column. The estimated values written into
``perimeter_m`` stay (we don't track which rooms were filled by this
migration, and downgrade should be safe to run on a hot DB without
losing user-editable data).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. Schema: add the column (idempotent) ---------------------
    room_columns = {c["name"] for c in inspector.get_columns("rooms")}
    if "perimeter_source" not in room_columns:
        op.add_column(
            "rooms",
            sa.Column("perimeter_source", sa.String(length=20), nullable=True),
        )

    # --- 2. Tag existing AI-imported perimeters as 'vision' ---------
    bind.execute(
        text(
            """
            UPDATE rooms
               SET perimeter_source = 'vision'
             WHERE perimeter_m IS NOT NULL
               AND source = 'ai'
               AND perimeter_source IS NULL
            """
        )
    )

    # --- 3. Tag existing manual perimeters as 'manual' --------------
    bind.execute(
        text(
            """
            UPDATE rooms
               SET perimeter_source = 'manual'
             WHERE perimeter_m IS NOT NULL
               AND source = 'manual'
               AND perimeter_source IS NULL
            """
        )
    )

    # --- 4. Estimate missing perimeters from area -------------------
    # 4 · sqrt(area) · 1.10 — see module docstring for the rationale.
    # We cast through ``numeric`` because the column is Numeric(10,3)
    # and PG's SQRT requires a float8 input; round to two decimals to
    # match the precision the UI displays and the LV exporter
    # consumes.
    bind.execute(
        text(
            """
            UPDATE rooms
               SET perimeter_m = ROUND(
                       (4 * SQRT(area_m2::float8) * 1.10)::numeric, 2
                   ),
                   perimeter_source = 'estimated'
             WHERE perimeter_m IS NULL
               AND area_m2 IS NOT NULL
               AND area_m2 > 0
            """
        )
    )

    # --- 5. Refresh wall-area cache for the freshly-filled rows -----
    # Three bounded UPDATEs. We split them so the SQL stays readable
    # — applied_factor first (independent), then gross (depends on
    # factor we just wrote), then net (depends on gross + opening
    # join).
    bind.execute(
        text(
            """
            UPDATE rooms
               SET applied_factor = CASE
                   WHEN is_staircase THEN 1.5
                   WHEN COALESCE(height_m, 2.50) > 4.0 THEN 1.16
                   WHEN COALESCE(height_m, 2.50) >= 3.0 THEN 1.12
                   ELSE 1.0
               END
             WHERE perimeter_source = 'estimated'
            """
        )
    )

    bind.execute(
        text(
            """
            UPDATE rooms
               SET wall_area_gross_m2 = ROUND(
                       (perimeter_m * COALESCE(height_m, 2.50)
                          * COALESCE(applied_factor, 1.0))::numeric,
                       2
                   )
             WHERE perimeter_source = 'estimated'
            """
        )
    )

    # Net: gross minus opening deductions ≥ 2.5 m² each, but only
    # when ``deductions_enabled`` is true. ``count`` multiplies the
    # individual opening area when there are several identical ones
    # in a single Opening row.
    bind.execute(
        text(
            """
            UPDATE rooms r
               SET wall_area_net_m2 = ROUND(
                       (
                           GREATEST(
                               COALESCE(r.wall_area_gross_m2, 0)
                               - CASE
                                     WHEN r.deductions_enabled THEN COALESCE(
                                         (
                                             SELECT SUM(
                                                 o.width_m
                                                 * o.height_m
                                                 * GREATEST(o.count, 1)
                                             )
                                               FROM openings o
                                              WHERE o.room_id = r.id
                                                AND (o.width_m * o.height_m) >= 2.5
                                         ),
                                         0
                                     )
                                     ELSE 0
                                 END,
                               0
                           )
                       )::numeric,
                       2
                   )
             WHERE r.perimeter_source = 'estimated'
            """
        )
    )


def downgrade() -> None:
    # Drop the column. The estimated ``perimeter_m`` values we wrote
    # stay in the rooms table — they're real user-editable numbers
    # now, not migration metadata, so dropping the source flag must
    # not also wipe the values themselves.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    room_columns = {c["name"] for c in inspector.get_columns("rooms")}
    if "perimeter_source" in room_columns:
        op.drop_column("rooms", "perimeter_source")
