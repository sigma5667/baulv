"""DSGVO Art. 7 evidence: consent_snapshots table + user version columns

Revision ID: 019
Revises: 018
Create Date: 2026-05-01

DS-1 — close the consent-evidence gap.

Background
----------

Until v23.2 we had ``users.marketing_email_opt_in`` as a boolean
flag but no record of *when* or *which version* of the legal
documents a user agreed to. Article 7(1) DSGVO requires the
controller to be able to demonstrate consent — a single boolean
fails that test the moment the privacy policy is amended.

The new ``consent_snapshots`` table is append-only: every
consent action (registration, privacy refresh, terms refresh,
marketing-opt-in toggle) writes a single row carrying the
versions in force at that moment plus forensic context (IP, UA).

The two new columns on ``users`` (``current_privacy_version``,
``current_terms_version``) hold the versions the user has CURRENTLY
accepted. Comparing them against ``app/legal_versions.py`` at
login time tells the SPA whether the user needs to re-accept.

Existing-user handling
----------------------

Backfilling consent for pre-v23.2 users is intentionally out of
scope here (per the DS-1 spec). The new columns default to NULL
for those rows; the SPA's ``ConsentRefreshModal`` treats NULL as
"grandfathered in", not as "needs refresh", so existing users
aren't ambushed by a modal on their next login. A separate
retroactive-consent campaign (DS-1 follow-up) will surface a
banner asking them to confirm.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, UUID as PG_UUID


revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. New table: consent_snapshots ---------------------------
    if "consent_snapshots" not in inspector.get_table_names():
        op.create_table(
            "consent_snapshots",
            sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                PG_UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("event_type", sa.String(length=50), nullable=False),
            sa.Column("privacy_version", sa.String(length=20), nullable=True),
            sa.Column("terms_version", sa.String(length=20), nullable=True),
            sa.Column(
                "marketing_optin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("ip_address", INET(), nullable=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        # Composite index matches the most likely query pattern: "show
        # me Maria's consent history newest-first". DESC on created_at
        # so the index serves the ORDER BY directly.
        op.create_index(
            "ix_consent_snapshots_user_created",
            "consent_snapshots",
            ["user_id", sa.text("created_at DESC")],
        )
        # Standalone index on event_type for the audit-viewer's
        # "filter by event" path; cheap, low cardinality.
        op.create_index(
            "ix_consent_snapshots_event_type",
            "consent_snapshots",
            ["event_type"],
        )

    # --- 2. Add the two version columns to users -------------------
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "current_privacy_version" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "current_privacy_version", sa.String(length=20), nullable=True
            ),
        )
    if "current_terms_version" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "current_terms_version", sa.String(length=20), nullable=True
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "current_terms_version" in user_cols:
        op.drop_column("users", "current_terms_version")
    if "current_privacy_version" in user_cols:
        op.drop_column("users", "current_privacy_version")

    if "consent_snapshots" in inspector.get_table_names():
        op.drop_index(
            "ix_consent_snapshots_event_type", table_name="consent_snapshots"
        )
        op.drop_index(
            "ix_consent_snapshots_user_created", table_name="consent_snapshots"
        )
        op.drop_table("consent_snapshots")
