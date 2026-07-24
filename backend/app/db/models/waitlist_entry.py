"""Warteliste-Eintrag (Double-Opt-In, v25).

Warum eine eigene Tabelle (und keine ``users``-Zeile)
=====================================================

Ein Warteliste-Eintrag ist bewusst KEIN Konto: es gibt kein Passwort,
keine AGB-Annahme, keinen Vertragsschluss. Die Landing-Page darf
werben, ohne dass juristisch mehr entsteht als eine dokumentierte
E-Mail-Marketing-Einwilligung (Double-Opt-In). Die Trennung von
``users`` hält diese Grenze auch im Datenmodell sichtbar.

Status-Maschine
===============

``pending`` → (Confirm-Link, binnen 7 Tagen) → ``confirmed``
``pending`` / ``confirmed`` → (Abmelde-Link) → ``unsubscribed``
``unsubscribed`` → (erneute Anmeldung) → ``pending`` (Re-Opt-in mit
frischem Token; ``unsubscribed_at`` bleibt als Historie stehen)

Nur ``confirmed``-Zeilen dürfen je Marketing-Mails bekommen — der
Admin-Endpoint liefert die Status-Trennung dafür frei Haus.

Storage-Modell
==============

``confirm_token_hash`` ist der SHA-256 des URL-safe-Tokens — nie der
Klartext. Gleiches Muster und gleiche Begründung wie
``PasswordResetToken.token_hash`` (Entropie steckt im Token, darum
schneller Hash statt bcrypt). Der Abmelde-Link braucht KEINE eigene
Spalte: er wird als HMAC aus ``jwt_secret`` + E-Mail abgeleitet und
ist damit jederzeit reproduzierbar (siehe
``app/services/waitlist.py``).

DSGVO-Notizen
=============

* ``signup_ip`` / ``confirmed_at`` / ``consent_text_version`` sind der
  Art.-7-Nachweis der Einwilligung ("wann, von wo, welchem Wortlaut
  zugestimmt").
* Nie bestätigte ``pending``-Zeilen werden vom Nightly-Cleanup
  gelöscht, sobald ihr Token ``WAITLIST_PENDING_GRACE_DAYS`` lang
  abgelaufen ist (Art. 5(1)(e) — ohne Bestätigung gibt es keinen
  Grund, die Adresse zu behalten). Siehe
  ``app/services/audit_cleanup.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow_aware() -> datetime:
    """tz-aware UTC ``now`` — gleiche Begründung wie im
    ``PasswordResetToken``-Model: SQLite (Test-Harness) vergleicht
    sonst naive gegen aware Timestamps."""
    return datetime.now(timezone.utc)


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'unsubscribed')",
            name="ck_waitlist_entries_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Beim Schreiben immer lowercase-normalisiert (Endpoint-Pflicht).
    # Unique — eine Adresse steht genau einmal auf der Liste; erneute
    # Anmeldungen aktualisieren die bestehende Zeile.
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    signup_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow_aware, nullable=False
    )
    # Best-effort Client-IP (X-Forwarded-For hinter Railway). Teil des
    # Art.-7-Einwilligungs-Nachweises.
    signup_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # SHA-256 hex des Bestätigungs-Tokens (64 Zeichen, fix). Bei
    # erneuter Anmeldung wird der Hash in-place ersetzt — der zuletzt
    # verschickte Link gewinnt.
    confirm_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_ip: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Version des Checkbox-Wortlauts (WAITLIST_CONSENT_VERSION zum
    # Zeitpunkt der Anmeldung) — Art.-7-Nachweis, analog zu den
    # ``consent_snapshots`` der registrierten User.
    consent_text_version: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    unsubscribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Kampagnen-Herkunft aus ``?ref=`` — bereits im Endpoint auf
    # ``[a-z0-9_-]{1,64}`` gefiltert, hier nur noch Speicher.
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
