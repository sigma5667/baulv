"""Pseudonymisierung von E-Mail-Adressen für Audit-Log und Server-Logs.

Eine Stelle für das Muster, das mit ``_email_audit_hash`` (v24.4.9,
``app/api/auth.py``) eingeführt wurde: ``sha256(analytics_salt + ':' +
email)``. Server-Logs (Railway, USA) sollen keine E-Mail-Adressen im
Klartext enthalten (DSGVO Art. 5 Abs. 1 lit. c, Datenminimierung);
über den Hash lässt sich trotzdem nachvollziehen, ob zwei Log-Zeilen
dieselbe Adresse betreffen.
"""

from __future__ import annotations

import hashlib

from app.config import settings

# Für Logzeilen reicht ein kurzer Präfix — lesbar, und dieselbe
# Adresse ergibt immer denselben Präfix wie im Audit-Log.
_LOG_HASH_CHARS = 12


def email_hash(email: str) -> str:
    """Voller 64-stelliger Hash — Format identisch zum Audit-Log."""
    return hashlib.sha256(
        f"{settings.analytics_salt}:{email}".encode("utf-8")
    ).hexdigest()


def email_log_hash(email: str | None) -> str:
    """Kurzform für Server-Logs, z. B. ``mail#3fa9c2e01b7d``.

    Normalisiert (trim + lowercase), damit Groß-/Kleinschreibung
    keine zweite Spur erzeugt."""
    if not email:
        return "mail#-"
    return "mail#" + email_hash(email.strip().lower())[:_LOG_HASH_CHARS]
