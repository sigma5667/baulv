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
    # Idempotenz-Hotfix. Hintergrund: der FastAPI-Lifespan rief
    # ``alembic upgrade head`` bei jedem Boot mit 2 gunicorn-Workern
    # PARALLEL. Worker 1 legte ``user_id`` an, Worker 2 stolperte mit
    # DuplicateColumnError → 027 blieb bei jedem Boot hängen
    # (``alembic_version`` evtl. noch auf 026, die Spalte existiert aber
    # bereits). Diese Version prüft JEDEN Schritt per Inspector und führt
    # nur das aus, was noch fehlt — ein erneuter Lauf heilt den halb-
    # fertigen Zustand UND verbucht 027 sauber. (Die Wurzelursache — der
    # parallele Lauf — schließt zusätzlich der Advisory-Lock im Lifespan,
    # siehe app/main.py:_run_migrations_with_lock.)
    bind = op.get_bind()

    def _has_column() -> bool:
        return any(
            c["name"] == "user_id"
            for c in sa.inspect(bind).get_columns("chat_sessions")
        )

    def _user_id_nullable() -> bool:
        for c in sa.inspect(bind).get_columns("chat_sessions"):
            if c["name"] == "user_id":
                return bool(c.get("nullable", True))
        return True

    def _has_user_fk() -> bool:
        # Prüfung über Spalten/Referenz-Tabelle statt über den Constraint-
        # Namen — deckt damit auch den auto-benannten Inline-FK der
        # Original-Migration ab.
        return any(
            fk.get("referred_table") == "users"
            and "user_id" in (fk.get("constrained_columns") or [])
            for fk in sa.inspect(bind).get_foreign_keys("chat_sessions")
        )

    def _has_index() -> bool:
        return any(
            ix["name"] == "ix_chat_sessions_user_id"
            for ix in sa.inspect(bind).get_indexes("chat_sessions")
        )

    # 1. Spalte (nullable) — nur wenn sie noch fehlt.
    if not _has_column():
        op.add_column(
            "chat_sessions",
            sa.Column("user_id", sa.Uuid(), nullable=True),
        )

    # 2. FK ON DELETE CASCADE — nur wenn auf user_id->users noch keiner
    #    existiert.
    if not _has_user_fk():
        op.create_foreign_key(
            "fk_chat_sessions_user_id_users",
            "chat_sessions", "users",
            ["user_id"], ["id"],
            ondelete="CASCADE",
        )

    # 3. Projektgebundene Sessions: Eigentümer aus dem Projekt erben.
    #    Inhärent idempotent — fasst nur NULL-Zeilen an.
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

    # 4. Herrenlose (projektlose / unattribuierbare) Alt-Sessions löschen.
    #    Inhärent idempotent — nur NULL-Zeilen. chat_messages cascaden.
    op.execute(sa.text("DELETE FROM chat_sessions WHERE user_id IS NULL"))

    # 5. NOT NULL — nur wenn aktuell nullable.
    if _user_id_nullable():
        op.alter_column("chat_sessions", "user_id", nullable=False)

    # 6. Index — nur wenn er noch fehlt.
    if not _has_index():
        op.create_index(
            "ix_chat_sessions_user_id", "chat_sessions", ["user_id"]
        )


def downgrade() -> None:
    # Auch der Downgrade prüft Existenz, damit er aus jedem Teilzustand
    # sauber durchläuft.
    bind = op.get_bind()
    if any(
        ix["name"] == "ix_chat_sessions_user_id"
        for ix in sa.inspect(bind).get_indexes("chat_sessions")
    ):
        op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    if any(
        c["name"] == "user_id"
        for c in sa.inspect(bind).get_columns("chat_sessions")
    ):
        op.drop_column("chat_sessions", "user_id")
