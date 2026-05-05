"""DSGVO-konforme Nutzungs-Analytics: Tabelle + 3 User-Spalten

Revision ID: 022
Revises: 021
Create Date: 2026-05-05

v23.8 — analytics opt-in pipeline
==================================

Three changes in this migration, all driven by the v23.8 spec:

  1. New table ``usage_analytics`` — one append-only row per
     event (project_created, lv_created, template_used, …). The
     row carries an *anonymised* user identifier (sha256 of the
     real user_id with a server-side salt) plus a per-event JSONB
     blob. Rows survive user deletion — that's the whole point:
     we want the aggregate "what do users do with BauLV?" signal
     even after individual accounts go away.

  2. New column ``users.analytics_consent`` — Boolean, default
     FALSE. The whole pipeline gates on this flag at the service
     layer; without explicit opt-in, no event ever lands in the
     table. DSGVO Art. 6 (1)(a) compliance: processing requires
     consent.

  3. New column ``users.industry_segment`` — String, nullable.
     User self-classifies as architect / builder / subcontractor
     / unknown. Captured in the analytics events so we can
     segment usage data by branch without ever joining back to
     the user row.

  4. New column ``users.is_admin`` — Boolean, default FALSE.
     Replaces the email-allowlist gate from v23.3 with a
     persistent flag. Used by the analytics dashboard endpoint to
     decide who sees aggregated metrics. The allowlist still works
     (it stays as a fallback in ``settings.admin_email_list``);
     the column is the new primary path.

DSGVO design notes
==================

* ``anonymous_user_id`` is a 64-char SHA-256 hex string, NOT the
  raw UUID. The salt lives in the application config
  (``settings.analytics_salt``); without it, no observer can
  correlate a hash back to a real user. Indexed for the per-user
  data-export endpoint.
* ``region_code`` is at the Bundesland level (e.g. ``AT-5`` for
  Salzburg). Town/street precision would re-identify users in
  rural regions where there's only one BauLV customer per village,
  so the service rounds it up before the row hits the DB.
* No FK from ``usage_analytics.anonymous_user_id`` to anything —
  the column is intentionally not joinable.
* Default ``analytics_consent = FALSE`` survives the migration
  because Postgres' DEFAULT clause runs at row creation; existing
  rows backfilled to FALSE explicitly so opt-in stays opt-in for
  pre-v23.8 accounts.

Idempotent
==========

All four changes are guarded by ``inspect()`` look-ups so a
re-run is a no-op. Standard for the v23.x migrations.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID


revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. usage_analytics table ----------------------------------
    if "usage_analytics" not in inspector.get_table_names():
        op.create_table(
            "usage_analytics",
            sa.Column(
                "id",
                PG_UUID(as_uuid=True),
                primary_key=True,
            ),
            sa.Column(
                "event_type",
                sa.String(length=50),
                nullable=False,
            ),
            # Sanitised event payload — service layer rejects any field
            # not on the per-event-type whitelist before it gets here.
            sa.Column(
                "event_data",
                JSONB(),
                nullable=True,
            ),
            # 64-char hex SHA-256 of (user_id + ANALYTICS_SALT). Not
            # joinable to the user row — that's the point.
            sa.Column(
                "anonymous_user_id",
                sa.String(length=64),
                nullable=False,
            ),
            # Bundesland-level only, e.g. "AT-5". NULL when the project
            # has no parseable address.
            sa.Column(
                "region_code",
                sa.String(length=10),
                nullable=True,
            ),
            # User-self-selected branch. NULL = ``unknown`` (we keep
            # the column nullable rather than defaulting to "unknown"
            # so analytics queries can distinguish "user didn't pick"
            # from "user picked unknown").
            sa.Column(
                "industry_segment",
                sa.String(length=30),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        # Composite index for the most common query pattern: "show
        # me events of this user, newest first" (used by the
        # per-user data-export endpoint).
        op.create_index(
            "ix_usage_analytics_user_time",
            "usage_analytics",
            ["anonymous_user_id", sa.text("created_at DESC")],
        )
        # Standalone index on event_type for the admin dashboard's
        # "count by event_type" aggregate. Cheap, low cardinality.
        op.create_index(
            "ix_usage_analytics_event_type",
            "usage_analytics",
            ["event_type"],
        )
        # Created_at index for time-window queries ("events in last
        # 30 days"). DESC so the dashboard's "newest first" pull is
        # an index-only scan.
        op.create_index(
            "ix_usage_analytics_created_at",
            "usage_analytics",
            [sa.text("created_at DESC")],
        )

    # --- 2-4. Three new columns on users ---------------------------
    user_cols = {c["name"] for c in inspector.get_columns("users")}

    if "analytics_consent" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "analytics_consent",
                sa.Boolean(),
                nullable=False,
                # Default FALSE per DSGVO Art. 7 — opt-in must be a
                # clear affirmative action. ``server_default`` covers
                # the backfill for existing rows; ``default`` covers
                # subsequent ORM-level inserts.
                server_default=sa.false(),
                default=False,
            ),
        )

    if "industry_segment" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "industry_segment",
                sa.String(length=30),
                nullable=True,
            ),
        )

    if "is_admin" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "is_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
                default=False,
            ),
        )

    # --- 5. analytics_consent column on consent_snapshots ---------
    # The DSGVO evidence ledger needs to capture the analytics
    # opt-in state at the moment of every snapshot, mirroring how
    # ``marketing_optin`` is captured today. Backfills to FALSE for
    # historical rows (pre-v23.8 snapshots had no analytics flag at
    # all, so FALSE is the truthful representation).
    if "consent_snapshots" in inspector.get_table_names():
        snap_cols = {
            c["name"] for c in inspector.get_columns("consent_snapshots")
        }
        if "analytics_consent" not in snap_cols:
            op.add_column(
                "consent_snapshots",
                sa.Column(
                    "analytics_consent",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                    default=False,
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "consent_snapshots" in inspector.get_table_names():
        snap_cols = {
            c["name"] for c in inspector.get_columns("consent_snapshots")
        }
        if "analytics_consent" in snap_cols:
            op.drop_column("consent_snapshots", "analytics_consent")

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "is_admin" in user_cols:
        op.drop_column("users", "is_admin")
    if "industry_segment" in user_cols:
        op.drop_column("users", "industry_segment")
    if "analytics_consent" in user_cols:
        op.drop_column("users", "analytics_consent")

    if "usage_analytics" in inspector.get_table_names():
        op.drop_index(
            "ix_usage_analytics_created_at", table_name="usage_analytics"
        )
        op.drop_index(
            "ix_usage_analytics_event_type", table_name="usage_analytics"
        )
        op.drop_index(
            "ix_usage_analytics_user_time", table_name="usage_analytics"
        )
        op.drop_table("usage_analytics")
