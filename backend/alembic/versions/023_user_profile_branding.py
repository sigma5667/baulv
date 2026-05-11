"""v24.3 — User-Profil-Felder fuer PDF-Branding

Revision ID: 023
Revises: 022
Create Date: 2026-05-11

Hintergrund
===========

Profi-Feedback am Mengenermittlungs-PDF (v23.9): der Header zeigte
"Erstellt von: kafjd (beta-test@baulv.at)" — Username + Email, was
fuer Bautraeger unprofessionell wirkt. v24.3 nimmt zwei neue Felder
auf den User auf, damit das PDF einen echten Briefkopf bekommt:

  * ``role``       — Funktion / Rolle des Erstellers ("Bautraeger",
                     "Architekt", "Generalunternehmer"). Frei
                     editierbar, nullable.
  * ``logo_path``  — Server-seitiger Pfad zum hochgeladenen
                     Firmenlogo (PNG/JPG, max 2 MB). NULL = kein
                     Logo, der PDF-Renderer faellt dann auf einen
                     dezenten "BauLV"-Text-Logo zurueck.

``full_name`` und ``company_name`` existieren seit v1.0 bereits auf
``users`` — die werden hier NICHT angefasst.

Idempotenz
==========

Beide Add-Column-Bloecke per ``inspect()`` geguarded — re-run ist
ein No-Op. Standard fuer die v2x.x-Migrationen.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_cols = {c["name"] for c in inspector.get_columns("users")}

    if "role" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "role",
                sa.String(length=100),
                nullable=True,
            ),
        )

    if "logo_path" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "logo_path",
                # 500 chars covers absolute paths in any sane upload
                # dir layout; the column is a server-managed file
                # reference, not user-typed text.
                sa.String(length=500),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_cols = {c["name"] for c in inspector.get_columns("users")}

    if "logo_path" in user_cols:
        op.drop_column("users", "logo_path")
    if "role" in user_cols:
        op.drop_column("users", "role")
