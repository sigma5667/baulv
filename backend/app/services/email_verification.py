"""E-Mail-Bestätigung (Double-Opt-In) für neue Konten (Audit 2026-11).

Zustandsloses Token statt eigener Tabelle: ``<user_id>.<expires_at>.<sig>``
mit ``sig = HMAC-SHA256(JWT_SECRET, "email-verify:<user_id>:<email>:<expires_at>")``.

* Die E-Mail-Adresse steckt in der Signatur — ändert sich die Adresse,
  wird ein alter Link ungültig.
* Ein JWT_SECRET-Wechsel macht offene Links ungültig; der User fordert
  im Banner einfach einen neuen an.
* Mehrfaches Öffnen ist harmlos (idempotent: ``email_verified_at``
  wird nur beim ersten Mal gesetzt).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from uuid import UUID

from app.config import settings

VERIFY_TOKEN_TTL_SECONDS = 72 * 60 * 60  # 3 Tage


def _sign(user_id: str, email: str, expires_at: int) -> str:
    payload = f"email-verify:{user_id}:{email.lower()}:{expires_at}".encode("utf-8")
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()


def mint_verify_token(*, user_id: UUID, email: str) -> str:
    expires_at = int(time.time()) + VERIFY_TOKEN_TTL_SECONDS
    uid = str(user_id)
    return f"{uid}.{expires_at}.{_sign(uid, email, expires_at)}"


def parse_verify_token(token: str) -> tuple[UUID, int, str] | None:
    """Zerlegt das Token in ``(user_id, expires_at, sig)`` oder ``None``.

    Prüft nur Form und Ablauf — die Signatur hängt an der aktuellen
    E-Mail des Users und wird in ``signature_matches`` geprüft.
    """
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    uid_str, exp_str, sig = parts
    try:
        user_id = UUID(uid_str)
        expires_at = int(exp_str)
    except ValueError:
        return None
    if expires_at <= int(time.time()):
        return None
    return user_id, expires_at, sig


def signature_matches(*, user_id: UUID, email: str, expires_at: int, sig: str) -> bool:
    expected = _sign(str(user_id), email, expires_at)
    return hmac.compare_digest(sig, expected)
