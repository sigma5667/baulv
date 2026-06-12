"""Security-Härtung (Audit 2026-06) — chat_sessions.user_id (Mandantentrennung)

Revision ID: 027
Revises: 026
Create Date: 2026-06-11

Hintergrund
===========

``chat_sessions`` trug bisher keinen Eigentümer. Eigentum existierte
nur transitiv über ``project_id`` — und für projektlose ("globale")
Sessions gar nicht. Das war ein IDOR: jeder authentifizierte User
konnte über ``GET /chat/sessions`` (ohne project_id) die Sessions
ALLER Mandanten auflisten und über ``GET/PATCH/DELETE
/chat/sessions/{id}`` bzw. ``POST .../messages`` fremde projektlose
Sessions lesen, umbenennen, löschen und beschreiben.

Diese Migration legt die Eigentümer-Spalte an und macht sie zur
Pflicht.

Backfill
========

1. Projektgebundene Sessions erben den Eigentümer aus dem Projekt
   (``projects.user_id``, seit Migration 007 vorhanden).
2. Verbleibende NULL-Zeilen sind die herrenlosen, projektlosen
   Alt-Sessions — nicht eindeutig einem User zuordenbar. Sie werden
   gelöscht (Nachrichten cascaden über die bestehende
   ``chat_messages.session_id``-FK mit ``ON DELETE CASCADE``). Damit
   bleibt keine unattribuierbare Session in der DB.
3. Erst danach wird die Spalte auf ``NOT NULL`` gesetzt.

Die FK nutzt ``ON DELETE CASCADE``: Chat-Sessions (personenbezogene
Inhalte) werden mit dem Konto gelöscht — deckt zugleich einen Teil der
DSGVO-Art.-17-Löschung sauber ab.

Reversibilität
==============

Downgrade droppt die Spalte wieder. Bereits in (2) gelöschte
herrenlose Sessions sind nicht wiederherstellbar — das ist
beabsichtigt (sie waren ohnehin nicht zuordenbar).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Spalte zunächst nullable anlegen (FK auf users, cascade).
    op.add_column(
        "chat_sessions",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    # 2. Projektgebundene Sessions: Eigentümer aus dem Projekt erben.
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            SET user_id = p.user_id
            FROM projects p
            WHERE cs.project_id = p.id
              AND cs.user_id IS NULL
            """
        )
    )

    # 3. Herrenlose (projektlose / unattribuierbare) Alt-Sessions
    #    löschen. chat_messages cascaden über ihre FK.
    op.execute(sa.text("DELETE FROM chat_sessions WHERE user_id IS NULL"))

    # 4. Jetzt Pflichtfeld + Index.
    op.alter_column("chat_sessions", "user_id", nullable=False)
    op.create_index(
        "ix_chat_sessions_user_id", "chat_sessions", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "user_id")
