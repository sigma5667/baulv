"""v24.4.3 — Room.is_active Flag

Revision ID: 025
Revises: 024
Create Date: 2026-05-18

Hintergrund
===========

Die Plan-Analyse-Pipeline extrahiert pro PDF typischerweise alle
sichtbaren Räume — Treppenhäuser, Balkone, Loggien inklusive. Für
viele Mengenermittlungen sind diese aber irrelevant (Balkone werden
oft separat ausgeschrieben, Treppenhäuser haben eigene Gewerke).

Vorher gab es nur die destruktive Option: ``DELETE /rooms/{id}``.
Wer einen Balkon irrtümlich gelöscht hat, musste den Vision-Pipeline-
Run wiederholen oder den Raum manuell neu anlegen.

``is_active`` ist die nicht-destruktive Alternative: der Raum bleibt
gespeichert, ist im UI sichtbar (grau + durchgestrichen), aber wird
aus der Aggregation, dem Mengenermittlungs-PDF und dem LV-Sync
ausgeklammert. Re-Aktivierung ist ein einzelner Toggle weiter — die
Wand-Calc-Cache wurde weitergeführt und steht sofort zur Verfügung.

Backfill
========

Bestandsdaten kriegen automatisch ``TRUE`` über den ``server_default``.
Kein expliziter ``UPDATE``-Statement nötig — die Spalte wird mit
``NOT NULL DEFAULT TRUE`` angelegt und PostgreSQL fan-outet den
Default beim ``ADD COLUMN`` auf alle existierenden Zeilen.

Idempotenz
==========

Die Migration ist NICHT idempotent — ein zweiter Lauf würde fehlschlagen
weil die Spalte schon existiert. Alembic verlässt sich auf seine
eigene ``alembic_version``-Tabelle und ruft jede Revision genau einmal
auf; das passt zum Standard-Verhalten und ist konsistent mit den
vorigen Migrationen (009, 011, 014 etc.).

Reversibilität
==============

Downgrade droppt die Spalte. Vorhandene ``is_active = FALSE``-Markierungen
gehen dabei verloren — aber das ist die einzig saubere Down-Operation,
weil nach einem Downgrade keine Stelle im Code mehr ``is_active``
lesen würde. Die User-Daten (Raum-Geometrie etc.) bleiben unangetastet.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rooms",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            # ``server_default`` lässt PostgreSQL alle Bestandszeilen
            # automatisch auf TRUE backfillen während ``ADD COLUMN``
            # läuft. Nach dem Backfill bleibt der Default in der
            # Spalten-Definition stehen, damit Inserts ohne explizites
            # ``is_active`` (z.B. der Vision-Pipeline-Pfad) den
            # erwarteten Aktiv-Default sehen.
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("rooms", "is_active")
