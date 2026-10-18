"""Admin-Rechte nur noch über users.is_admin (Security-Fix 2026-10)

Revision ID: 029
Revises: 028
Create Date: 2026-10-18

Hintergrund
===========

Bis v25 entschied zusätzlich die E-Mail-Allowlist ``ADMIN_EMAILS``
über Admin-Rechte. Die Registrierung prüft nicht, ob jemand die
angegebene Adresse besitzt — eine gelistete, aber noch unregistrierte
Adresse hätte ein Fremder registrieren und damit Admin werden können.
Ab jetzt zählt nur noch das Flag ``users.is_admin``.

Damit bestehende Admins nicht ausgesperrt werden, setzt diese
Migration **einmalig** das Flag für die Konten, die zum Deploy-
Zeitpunkt mit einer Adresse aus ``ADMIN_EMAILS`` existieren. Später
registrierte Konten bekommen es nicht mehr — genau das schließt die
Lücke.

Idempotenz
==========

Das ``UPDATE`` trifft nur Konten mit ``is_admin = FALSE`` — ein zweiter
Lauf ändert nichts. Leere oder fehlende Allowlist → keine Änderung an
``users``.

Reversibilität
==============

Die IDs der Konten, die *diese* Migration auf ``is_admin = TRUE``
gesetzt hat, landen in der Merk-Tabelle ``migration_029_promoted``.
Der Downgrade setzt genau diese Konten zurück auf ``FALSE`` und
löscht die Merk-Tabelle. Flags, die schon vorher gesetzt waren,
bleiben unangetastet. Es werden keine Daten gelöscht oder sonst
überschrieben.

Achtung: Downgrade allein stellt den alten Zustand nur zusammen mit
dem alten Code wieder her (``git revert`` des Security-Commits) — der
alte Code wertet ``ADMIN_EMAILS`` wieder aus. Mit dem neuen Code und
ohne Flag hätte niemand Admin-Rechte.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import settings


revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TRACKING_TABLE = "migration_029_promoted"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"CREATE TABLE IF NOT EXISTS {_TRACKING_TABLE} "
            "(user_id UUID PRIMARY KEY)"
        )
    )

    emails = sorted(settings.admin_email_list)
    if not emails:
        logger.info("029: ADMIN_EMAILS leer — kein Konto übernommen")
        return

    promoted = bind.execute(
        sa.text(
            "UPDATE users SET is_admin = TRUE "
            "WHERE lower(email) IN :emails AND is_admin = FALSE "
            "RETURNING id"
        ).bindparams(sa.bindparam("emails", expanding=True)),
        {"emails": emails},
    ).scalars().all()
    for user_id in promoted:
        bind.execute(
            sa.text(
                f"INSERT INTO {_TRACKING_TABLE} (user_id) VALUES (:id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": user_id},
        )
    logger.info(
        "029: %s bestehende(s) Konto/Konten aus ADMIN_EMAILS auf "
        "is_admin=TRUE übernommen (%s Adresse(n) gelistet)",
        len(promoted),
        len(emails),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TRACKING_TABLE):
        return
    result = bind.execute(
        sa.text(
            "UPDATE users SET is_admin = FALSE "
            f"WHERE id IN (SELECT user_id FROM {_TRACKING_TABLE})"
        )
    )
    logger.info(
        "029 downgrade: %s Konto/Konten wieder auf is_admin=FALSE",
        result.rowcount,
    )
    bind.execute(sa.text(f"DROP TABLE {_TRACKING_TABLE}"))
