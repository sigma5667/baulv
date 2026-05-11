"""v24.3.1 — Backfill NULL room heights to the 2.50 m default

Revision ID: 024
Revises: 023
Create Date: 2026-05-11

Hintergrund
===========

Pre-v24.3.1 schrieb die KI-Pipeline (``_store_extraction_result``)
keinen Default-Height-Writeback. Raeume, fuer die Vision keine Hoehe
gefunden hatte (was bei Grundriss-Uploads die Regel ist — Hoehen
stehen typischerweise im Schnitt), landeten mit ``height_m=NULL``
und ``ceiling_height_source='default'`` in der DB. Die Wand-Calc-
Cache war trotzdem mit dem 2,50-Fallback berechnet — die UI zeigte
einen Display-Override-Wert von 2,50 mit Standard-Pille, das
Mengenermittlungs-PDF aber rendete "—" aus dem ehrlichen NULL.

Vater-Feedback (2026-05-11): "Im Mengenermittlungs-PDF stehen keine
Hoehenangaben." Symptom: User sieht in der UI 2,50 ueberall, im PDF
fehlen die Werte. Wenn der User auf das UI-Cell klickte und 2,50
"bestaetigte", verschluckte ein ``InlineNumericEdit``-Same-Value-
Guard den Save (parsed=2.5 === value=2.5 vom Display-Override
→ skip).

v24.3.1 fixt den Pipeline-Pfad (immer Writeback) und entfernt den
Frontend-Override (UI wird ehrlich). Diese Migration backfillt
Bestandsdaten, damit aktuelle Bestandsprojekte sofort konsistente
Werte zeigen — ohne Backfill wuerden Vaters 15 Raeume im PDF
weiterhin "—" zeigen.

Backfill-Regel
==============

  UPDATE rooms
  SET height_m = 2.50
  WHERE height_m IS NULL
    AND ceiling_height_source = 'default';

Bewusst eng:
- Nur ``default``-Quellen — wir ueberschreiben keine "manual"-,
  "schnitt"- oder "grundriss"-getaggten Werte (die haben einen
  echten Sinn fuer NULL, der von einer expliziten Benutzeraktion
  herruehrt).
- Nur ``height_m IS NULL`` — wir veraendern keine vorhandenen
  Werte (z.B. falsch interpretierte "default"-Quellen mit
  vorhandenem Wert bleiben unangetastet).

Idempotenz
==========

Sicher mehrfach ausfuehrbar. Beim zweiten Lauf findet die WHERE-
Bedingung keine Zeilen mehr.

Reversibilitaet
===============

Downgrade ist absichtlich ein No-Op: wir koennen 2,50-Werte, die
in der Zeit zwischen Up- und Downgrade entstanden sind, nicht von
echten gepflegten 2,50-Werten unterscheiden. Im Zweifelsfall liefe
ein Revert Gefahr, eine vom Anwender bestaetigte 2,50-Eingabe
auf NULL zurueckzusetzen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE rooms
        SET height_m = 2.50
        WHERE height_m IS NULL
          AND ceiling_height_source = 'default'
        """
    )


def downgrade() -> None:
    # Intentional no-op — see module docstring.
    pass
