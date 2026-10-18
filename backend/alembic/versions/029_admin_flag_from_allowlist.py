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

Reines ``UPDATE ... WHERE is_admin = FALSE`` — ein zweiter Lauf ändert
nichts. Leere oder fehlende Allowlist → kein Statement.

Reversibilität
==============

Downgrade ist ein No-op: welche Flags vorher schon gesetzt waren,
lässt sich nicht rekonstruieren, und Flags zu entfernen würde Admins
aussperren.
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


def upgrade() -> None:
    emails = sorted(settings.admin_email_list)
    if not emails:
        logger.info("029: ADMIN_EMAILS leer — kein Konto übernommen")
        return
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "UPDATE users SET is_admin = TRUE "
            "WHERE lower(email) IN :emails AND is_admin = FALSE"
        ).bindparams(sa.bindparam("emails", expanding=True)),
        {"emails": emails},
    )
    logger.info(
        "029: %s bestehende(s) Konto/Konten aus ADMIN_EMAILS auf "
        "is_admin=TRUE übernommen (%s Adresse(n) gelistet)",
        result.rowcount,
        len(emails),
    )


def downgrade() -> None:
    pass
