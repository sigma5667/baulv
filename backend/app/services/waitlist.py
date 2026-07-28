"""Warteliste-Token-Lebenszyklus + Consent-Pins (v25).

Zwei Token-Arten, ein Modul:

* **Confirm-Token** (Double-Opt-In): zufällig, 7 Tage gültig, als
  SHA-256 in ``waitlist_entries.confirm_token_hash`` — exakt das
  Muster aus ``app/services/password_reset.py`` (Entropie steckt im
  Token → schneller Hash statt bcrypt, Klartext nur im Mail-Body).
  Einmal-Nutzung entsteht über den Status-Übergang
  ``pending → confirmed``; ein zweiter Confirm auf denselben Token
  findet keine ``pending``-Zeile mehr.

* **Abmelde-Token**: KEIN Zufallswert und KEINE eigene DB-Spalte,
  sondern ``base64url(email) + "." + HMAC-SHA256(jwt_secret, email)``.
  Grund: in der DB liegt nur der Confirm-*Hash* — für eine spätere
  Marketing-Mail ließe sich daraus kein Abmelde-Link mehr bauen. Der
  HMAC-Token ist dagegen jederzeit aus der E-Mail-Adresse
  reproduzierbar, läuft nie ab (Abmelde-Links in alten Mails müssen
  ewig funktionieren) und trägt seine E-Mail selbst — der Endpoint
  kann sie ohne DB-Scan verifizieren. Schlimmster Missbrauchsfall
  eines geleakten Tokens: jemand meldet die Adresse ab. Das ist
  bewusst akzeptiert.

Consent-Pins
============

``WAITLIST_CONSENT_VERSION`` + ``WAITLIST_CONSENT_TEXT`` pinnen den
Checkbox-Wortlaut, den jede Anmeldung als Art.-7-Nachweis in
``consent_text_version`` referenziert (gleiches Prinzip wie
``app/legal_versions.py``). Sie liegen ABSICHTLICH hier und nicht in
``legal_versions.py``: die Datei trägt gerade uncommittete
AGB-Änderungen, und das Warteliste-Feature muss als eigener Commit
sauber davon trennbar bleiben. Beim Bump gilt die gleiche Zwei-
Schritt-Regel: Konstante hier UND Checkbox-Text in
``frontend/src/pages/LandingPage.tsx`` gemeinsam ändern.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.config import settings

# v1.0 (2026-07-19) — Erst-Wortlaut: nur Start-Benachrichtigung.
# v1.1 (2026-07-24) — Zweck erweitert auf regelmäßige Entwicklungs-
# Updates + Widerrufs-Hinweis in den Wortlaut gehoben. Gebumpt BEVOR
# die Liste je öffentlich war (WAITLIST_ENABLED war nie an) — es
# existieren keine v1.0-Einwilligungen, daher kein Re-Consent nötig.
WAITLIST_CONSENT_VERSION: str = "1.1"
WAITLIST_CONSENT_TEXT: str = (
    "Ich möchte per E-Mail über den Entwicklungsstand und den Start "
    "von BauLV informiert werden. Die Einwilligung kann ich jederzeit "
    "über den Abmelde-Link widerrufen."
)

# 7 Tage — lang genug für "Mail am Freitag, Klick am Montag", kurz
# genug, dass verwaiste pending-Zeilen zeitnah aufräumbar werden.
CONFIRM_TOKEN_TTL = timedelta(days=7)

# 32 Bytes URL-safe ≈ 256 Bit Entropie — gleicher Wert wie beim
# Passwort-Reset-Token.
CONFIRM_TOKEN_BYTES = 32

# Domain-Separation für den Abmelde-HMAC: derselbe ``jwt_secret``
# signiert auch Access-Tokens und den Beta-Gate-HMAC. Das Präfix
# stellt sicher, dass ein Waitlist-Abmelde-Token nie als etwas
# anderes verifiziert (und umgekehrt).
_UNSUBSCRIBE_CONTEXT = "baulv-waitlist-unsubscribe:"


def hash_confirm_token(token: str) -> str:
    """SHA-256 hex des URL-safe-Tokens. 64 Zeichen, lowercase."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_confirm_token() -> tuple[str, str, datetime]:
    """Frischen Confirm-Token erzeugen.

    Returns ``(plaintext, sha256_hex, expires_at)``. Der Klartext
    gehört in den Mail-Link und wird danach verworfen — er erreicht
    nie die DB und nie ein Log (gleiche Disziplin wie beim
    Passwort-Reset).
    """
    plaintext = secrets.token_urlsafe(CONFIRM_TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + CONFIRM_TOKEN_TTL
    return plaintext, hash_confirm_token(plaintext), expires_at


def _unsubscribe_signature(email: str) -> str:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        (_UNSUBSCRIBE_CONTEXT + email).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mint_unsubscribe_token(email: str) -> str:
    """Selbstbeschreibender Abmelde-Token für ``email`` (lowercase
    erwartet): ``base64url(email).hmac_hex``.

    Deterministisch — derselbe Aufruf liefert immer denselben Token,
    darum kann jede künftige Mail an dieselbe Adresse denselben
    Abmelde-Link tragen, ohne dass irgendetwas gespeichert wird.
    """
    payload = (
        base64.urlsafe_b64encode(email.encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    return f"{payload}.{_unsubscribe_signature(email)}"


def resolve_unsubscribe_token(token: str) -> str | None:
    """E-Mail aus einem Abmelde-Token zurückgewinnen.

    Returns die (lowercase) E-Mail bei gültiger HMAC-Signatur, sonst
    ``None``. Konstante-Zeit-Vergleich via ``hmac.compare_digest`` —
    der Token ist zwar kein Hochsicherheits-Credential, aber der
    Vergleich kostet nichts.
    """
    payload, sep, signature = token.partition(".")
    if not sep or not payload or not signature:
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        email = base64.urlsafe_b64decode(padded.encode("ascii")).decode(
            "utf-8"
        )
    except (ValueError, UnicodeDecodeError):
        return None
    if not email:
        return None
    if not hmac.compare_digest(signature, _unsubscribe_signature(email)):
        return None
    return email
