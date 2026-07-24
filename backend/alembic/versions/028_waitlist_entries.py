"""Warteliste (Double-Opt-In) — waitlist_entries (v25)

Revision ID: 028
Revises: 027
Create Date: 2026-07-19

Hintergrund
===========

Öffentliche Warteliste für die Landing-Page: E-Mail-Anmeldung mit
Double-Opt-In, damit geworben werden kann, ohne dass ein Vertrag
zustande kommt. Kein Bezug zu ``users`` — ein Warteliste-Eintrag ist
bewusst kein Konto (siehe Model-Docstring in
``app/db/models/waitlist_entry.py``).

Idempotenz
==========

Gleiche Bauart wie 027: jeder Schritt prüft per Inspector, ob er noch
nötig ist, damit ein halb-fertiger Zustand (paralleler Boot, Crash
mitten in der Migration) beim nächsten Lauf geheilt statt zum
Dauerfehler wird. Die Wurzelursache paralleler Läufe schließt weiter
der Advisory-Lock im Lifespan (``app/main.py:_run_migrations_with_lock``);
die Inspector-Checks hier sind das Sicherheitsnetz darunter.

Reversibilität
==============

Downgrade droppt die Tabelle samt Einträgen. Warteliste-Daten sind
reine Marketing-Einwilligungen ohne abhängige Tabellen — der Verlust
beim Downgrade ist akzeptiert.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "waitlist_entries"


def upgrade() -> None:
    bind = op.get_bind()

    def _has_table() -> bool:
        return sa.inspect(bind).has_table(_TABLE)

    def _has_index(name: str) -> bool:
        return any(
            ix["name"] == name
            for ix in sa.inspect(bind).get_indexes(_TABLE)
        )

    # 1. Tabelle — nur wenn sie noch fehlt. Constraints inline, damit
    #    der Create ein einziger atomarer Schritt ist.
    if not _has_table():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("company_name", sa.String(200), nullable=False),
            sa.Column("name", sa.String(200), nullable=True),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "signup_at", sa.DateTime(timezone=True), nullable=False
            ),
            sa.Column("signup_ip", sa.String(64), nullable=True),
            sa.Column(
                "confirm_token_hash", sa.String(64), nullable=False
            ),
            sa.Column(
                "token_expires_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "confirmed_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("confirmed_ip", sa.String(64), nullable=True),
            sa.Column(
                "consent_text_version", sa.String(20), nullable=False
            ),
            sa.Column(
                "unsubscribed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column("source", sa.String(64), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'confirmed', 'unsubscribed')",
                name="ck_waitlist_entries_status",
            ),
            sa.UniqueConstraint(
                "email", name="uq_waitlist_entries_email"
            ),
            sa.UniqueConstraint(
                "confirm_token_hash",
                name="uq_waitlist_entries_confirm_token_hash",
            ),
        )

    # 2. Indizes — einzeln geprüft, falls ein früherer Lauf zwischen
    #    Tabelle und Index abgebrochen ist.
    if not _has_index("ix_waitlist_entries_email"):
        op.create_index("ix_waitlist_entries_email", _TABLE, ["email"])
    if not _has_index("ix_waitlist_entries_confirm_token_hash"):
        op.create_index(
            "ix_waitlist_entries_confirm_token_hash",
            _TABLE,
            ["confirm_token_hash"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
