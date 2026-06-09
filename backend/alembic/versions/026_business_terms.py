"""v24.4.8 — Unternehmer-Bestätigung (B2B-Abgrenzung gegen FAGG/KSchG)

Revision ID: 026
Revises: 025
Create Date: 2026-06-09

Hintergrund
===========

BauLV ist ein B2B-SaaS — ausschließlich für Unternehmer iSd § 1 UGB
(Bauträger, Baufirmen, Architekten, Ziviltechniker, Sachverständige).
Damit das Angebot nicht versehentlich unter den Verbraucherschutz
(KSchG / FAGG) fällt, fragen wir die Unternehmer-Eigenschaft als
Pflicht-Bestätigung ab — einmal bei der Registrierung und einmal
am Vertragsschluss-Moment (Stripe-Checkout).

Diese Migration legt die zwei dafür nötigen Spalten an:

  1. ``users.current_business_terms_version`` — der Versions-String
     den dieser User aktuell akzeptiert hat. NULL ist der
     "grandfathered"-Zustand (Bestandsuser vor v24.4.8). Das Backend
     lehnt vor jedem Stripe-Checkout 400 ab, wenn der Wert nicht zur
     kanonischen ``BUSINESS_TERMS_VERSION`` aus ``app/legal_versions.py``
     passt — Bestandsuser werden über den ``ConsentRefreshModal`` zum
     erneuten Akzeptieren geführt (genau wie bei einem Privacy/Terms-
     Bump).

  2. ``consent_snapshots.business_terms_version`` — DSGVO Art. 7
     Evidence-Spalte parallel zu ``privacy_version`` / ``terms_version``.
     Damit kann später für jeden einzelnen Snapshot rekonstruiert
     werden, welchen Wortlaut der User in dem Moment akzeptiert hat,
     selbst wenn die Klausel danach mehrfach gebumpt wurde.

Backfill
========

Beide Spalten bleiben NULL für Bestandszeilen — bewusst kein
``server_default`` und keine ``UPDATE``-Anweisung. Bestandsuser
sollen explizit reconsenten (via ConsentRefreshModal), nicht
stillschweigend als "hat bestätigt" markiert werden — sonst gäbe es
keinen Beweis-Snapshot für deren Unternehmer-Eigenschaft.

Reversibilität
==============

Downgrade droppt die beiden Spalten. Vorhandene Beweis-Snapshots
mit ``business_terms_version != NULL`` gehen dabei verloren — das
ist die einzig saubere Down-Operation, weil nach dem Downgrade
kein Code mehr existiert, der die Spalte lesen könnte. User-Daten
selbst (Konten, Käufe) sind nicht betroffen.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "current_business_terms_version",
            sa.String(length=20),
            nullable=True,
        ),
    )
    op.add_column(
        "consent_snapshots",
        sa.Column(
            "business_terms_version",
            sa.String(length=20),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("consent_snapshots", "business_terms_version")
    op.drop_column("users", "current_business_terms_version")
