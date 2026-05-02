"""rooms.plan_id FK: switch to ON DELETE SET NULL

Revision ID: 017
Revises: 016
Create Date: 2026-05-01

Background
----------

``Room.plan_id`` was originally declared without an ``ondelete``
clause, which means Postgres uses ``RESTRICT`` — deleting a Plan
would be blocked while any Room still references it. That blocked
the v23 plan-deletion feature: we want users to be able to delete a
plan and either keep the rooms (with their plan link cleared) or
delete the rooms along with it.

Switching to ``ON DELETE SET NULL`` cleanly encodes the
``delete_rooms=false`` path: when the plan row goes away, every
referencing room's ``plan_id`` becomes ``NULL`` automatically,
without the endpoint having to do an extra UPDATE first. Manual
rooms (which already have ``plan_id IS NULL``) are untouched.

Mechanics
---------

The auto-generated FK name on Postgres is ``rooms_plan_id_fkey``.
We discover it via the inspector instead of hard-coding so a
locally-different name (e.g. left over from an older alembic run)
doesn't trip the migration.

Tests use ``Base.metadata.create_all`` against SQLite which doesn't
exercise this migration; the SQLAlchemy model has been updated in
parallel so unit tests still see the SET NULL semantics on the
ORM side.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _find_plan_fk_name(inspector: sa.engine.reflection.Inspector) -> str | None:
    """Return the existing FK constraint name on rooms.plan_id, or None."""
    for fk in inspector.get_foreign_keys("rooms"):
        if fk.get("referred_table") == "plans" and "plan_id" in (
            fk.get("constrained_columns") or []
        ):
            return fk.get("name")
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    fk_name = _find_plan_fk_name(inspector)
    if fk_name:
        op.drop_constraint(fk_name, "rooms", type_="foreignkey")

    # Recreate with the SET NULL ondelete behaviour. Use the canonical
    # name so future inspections find it predictably.
    op.create_foreign_key(
        "rooms_plan_id_fkey",
        "rooms",
        "plans",
        ["plan_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    fk_name = _find_plan_fk_name(inspector)
    if fk_name:
        op.drop_constraint(fk_name, "rooms", type_="foreignkey")

    # Restore the default RESTRICT behaviour.
    op.create_foreign_key(
        "rooms_plan_id_fkey",
        "rooms",
        "plans",
        ["plan_id"],
        ["id"],
    )
