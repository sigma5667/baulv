"""E-Mail-Bestätigung: users.email_verified_at (Audit 2026-11)

Revision ID: 030
Revises: 029
Create Date: 2026-11-02

Hintergrund
===========

Bis v25 hat die Registrierung nie geprüft, ob jemand die angegebene
E-Mail-Adresse besitzt. Ab jetzt sind die KI-Funktionen erst nach
Klick auf den Bestätigungs-Link nutzbar (``email_verified_at`` NOT
NULL, siehe ``subscriptions.require_feature``).

Erlaubnisliste statt Pauschal-Backfill
======================================

Bestandskonten werden **nicht** pauschal als bestätigt übernommen —
in der offenen Phase konnte sich jeder per API ohne Beta-Code
registrieren. Bestätigt wird nur, wessen Konto-ID in der
Umgebungsvariable ``MIGRATION_030_VERIFIED_USER_IDS`` steht
(kommagetrennte UUIDs, vom Betreiber als echt bestätigt). Alle anderen
Konten — auch solche, die bis zum Deploy neu entstehen — müssen den
Link selbst anfordern. IDs statt E-Mail-Adressen, damit keine
personenbezogenen Daten im Git landen; die Variable nach dem Deploy
wieder entfernen.

Ein Eintrag, der keine UUID ist oder zu keinem Konto passt, bricht die
Migration ab (Tippfehler sollen nicht still ein echtes Konto
aussperren).

Idempotenz / Reversibilität
===========================

Das ``UPDATE`` trifft nur Konten mit ``email_verified_at IS NULL``.
Der Downgrade entfernt die Spalte; es werden keine anderen Daten
geändert.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

ALLOWLIST_ENV = "MIGRATION_030_VERIFIED_USER_IDS"


def _parse_allowlist(raw: str) -> list[str]:
    ids: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(str(uuid.UUID(part)))
        except ValueError as exc:
            raise RuntimeError(
                f"{ALLOWLIST_ENV}: Eintrag ist keine gültige Konto-ID (UUID)"
            ) from exc
    return ids


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    ids = _parse_allowlist(os.environ.get(ALLOWLIST_ENV, ""))
    if not ids:
        logger.warning(
            "030: %s leer — kein Bestandskonto als bestätigt markiert",
            ALLOWLIST_ENV,
        )
        return

    conn = op.get_bind()
    found = {
        str(row[0])
        for row in conn.execute(
            sa.text("SELECT id FROM users WHERE CAST(id AS TEXT) IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise RuntimeError(
            f"030: {len(missing)} ID(s) aus {ALLOWLIST_ENV} gehören zu keinem "
            "Konto — Migration abgebrochen, bitte Liste prüfen"
        )

    result = conn.execute(
        sa.text(
            "UPDATE users SET email_verified_at = now() "
            "WHERE email_verified_at IS NULL AND CAST(id AS TEXT) IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    logger.info("030: %d Bestandskonto/-konten als bestätigt markiert", result.rowcount)


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
