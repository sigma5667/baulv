"""Tests für den Beta-Gate (``app.api.beta_gate``).

Drei thematische Blöcke:

1. **Signatur + Token-Format** — Sichert, dass _sign/_verify_token sich
   gegenseitig akzeptieren, dass abgelaufene Tokens abgelehnt werden
   und dass die Code-Rotation alte Tokens invalidiert (Tobi's
   Hauptanforderung: Railway-ENV ändern → alle Sessions weg).

2. **Konfigurations-Fail-Safe** — Sichert, dass ein unkonfigurierter
   Gate (``BETA_ACCESS_CODE = ""``) jede Eingabe ablehnt und jedes
   Token ungültig macht. Verhindert das Disaster-Szenario "Deploy
   vergessen → Gate offen".

3. **Rate-Limit** — 10 Versuche/Minute/IP. Locks the constant so eine
   versehentliche Erhöhung sofort rot wird.

Wir testen direkt gegen die Helper-Funktionen ohne FastAPI-TestClient,
in derselben Manier wie ``test_password_strength.py``. Das hält die
Tests schnell und ohne DB-Fixture-Abhängigkeit.
"""

from __future__ import annotations

import time

import pytest

from app.api import beta_gate
from app.config import settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def with_code(monkeypatch):
    """Setzt einen festen ``BETA_ACCESS_CODE`` für den Test."""
    monkeypatch.setattr(settings, "beta_access_code", "TestCode-2026-XYZ")
    yield "TestCode-2026-XYZ"


@pytest.fixture
def no_code(monkeypatch):
    """Setzt ``BETA_ACCESS_CODE`` auf leer — Gate-Fail-Safe-Modus."""
    monkeypatch.setattr(settings, "beta_access_code", "")
    yield


@pytest.fixture(autouse=True)
def reset_rate_limit():
    """Wipe in-memory rate-limit state zwischen Tests."""
    beta_gate._reset_rate_limit()
    yield
    beta_gate._reset_rate_limit()


# ---------------------------------------------------------------------------
# Block 1 — Signatur + Token
# ---------------------------------------------------------------------------


def test_issued_token_verifies(with_code):
    """Frisch ausgestelltes Token muss sofort akzeptiert werden."""
    token = beta_gate._issue_token()
    assert beta_gate._verify_token(token) is True


def test_token_format_is_expires_dot_signature(with_code):
    """Format ist ``<unix_ts>.<hex_sig>`` — beide Teile vorhanden."""
    token = beta_gate._issue_token()
    parts = token.split(".")
    assert len(parts) == 2
    assert parts[0].isdigit(), "expires_at part must be numeric"
    # SHA-256-Hex = 64 chars.
    assert len(parts[1]) == 64
    assert all(c in "0123456789abcdef" for c in parts[1])


def test_expired_token_is_rejected(with_code):
    """Token mit Past-Expiry muss abgelehnt werden, auch bei gültiger Signatur."""
    past_expiry = int(time.time()) - 3600
    sig = beta_gate._sign(past_expiry, with_code)
    expired_token = f"{past_expiry}.{sig}"
    assert beta_gate._verify_token(expired_token) is False


def test_malformed_token_is_rejected(with_code):
    """Tokens ohne Punkt, mit nicht-numerischem Expiry oder leer → False."""
    assert beta_gate._verify_token("") is False
    assert beta_gate._verify_token("garbage-no-dot") is False
    assert beta_gate._verify_token("notanumber.abc123") is False
    # Numerischer Expiry, aber Signatur passt nicht.
    future = int(time.time()) + 3600
    assert beta_gate._verify_token(f"{future}.0000") is False


def test_code_rotation_invalidates_existing_token(monkeypatch):
    """Tobi's Hauptanforderung: Railway-ENV ändern → alte Tokens weg.

    Wir stellen ein Token mit Code A aus, rotieren auf Code B,
    dann muss ``_verify_token`` False zurückgeben — auch wenn das
    Expiry-Datum noch in der Zukunft liegt.
    """
    monkeypatch.setattr(settings, "beta_access_code", "OldCode-AAA")
    token = beta_gate._issue_token()
    # Sanity: vor Rotation gültig.
    assert beta_gate._verify_token(token) is True
    # Rotation.
    monkeypatch.setattr(settings, "beta_access_code", "NewCode-BBB")
    # Nach Rotation muss das alte Token ungültig sein.
    assert beta_gate._verify_token(token) is False


def test_tampered_signature_is_rejected(with_code):
    """Manipulation am Sig-Teil → Token ungültig (kein Bypass durch byte-flip)."""
    token = beta_gate._issue_token()
    expires, sig = token.split(".", 1)
    # Erstes Hex-Zeichen flippen.
    flipped = ("1" if sig[0] != "1" else "2") + sig[1:]
    tampered = f"{expires}.{flipped}"
    assert beta_gate._verify_token(tampered) is False


def test_tampered_expiry_is_rejected(with_code):
    """Manipulation am Expiry-Teil bricht die Signatur → ungültig."""
    token = beta_gate._issue_token()
    _expires, sig = token.split(".", 1)
    # Expiry weit in die Zukunft schreiben, aber Signatur lassen
    far_future = int(time.time()) + 10 * 365 * 86400
    tampered = f"{far_future}.{sig}"
    assert beta_gate._verify_token(tampered) is False


def test_ttl_is_thirty_days():
    """Locked: 30 Tage. Eine versehentliche Absenkung würde Vater
    alle 24h zum Code-Tippen zwingen."""
    assert beta_gate.TOKEN_TTL_SECONDS == 30 * 24 * 60 * 60


# ---------------------------------------------------------------------------
# Block 2 — Fail-Safe für unkonfigurierten Gate
# ---------------------------------------------------------------------------


def test_unconfigured_gate_rejects_every_token(no_code):
    """``BETA_ACCESS_CODE = ""`` → kein Token kann verifizieren."""
    # Auch ein "korrekt aussehendes" Token muss durchfallen.
    far_future = int(time.time()) + 3600
    fake_token = f"{far_future}.{'a' * 64}"
    assert beta_gate._verify_token(fake_token) is False


def test_unconfigured_gate_signature_with_empty_code(no_code):
    """Selbst wenn jemand mit dem leeren String signiert, lehnt
    ``_verify_token`` ab (Early-Return wegen leerer Config)."""
    far_future = int(time.time()) + 3600
    sig_with_empty = beta_gate._sign(far_future, "")
    token = f"{far_future}.{sig_with_empty}"
    assert beta_gate._verify_token(token) is False


# ---------------------------------------------------------------------------
# Block 3 — Rate-Limit
# ---------------------------------------------------------------------------


def test_rate_limit_allows_up_to_max():
    """Genau ``RATE_LIMIT_MAX`` Versuche pro Window müssen durchgehen."""
    for _ in range(beta_gate.RATE_LIMIT_MAX):
        allowed, _ = beta_gate._check_rate_limit("1.2.3.4")
        assert allowed is True


def test_rate_limit_blocks_after_max():
    """Versuch Nummer ``MAX+1`` muss abgelehnt werden, mit Retry-After > 0."""
    for _ in range(beta_gate.RATE_LIMIT_MAX):
        beta_gate._check_rate_limit("1.2.3.4")
    allowed, retry_after = beta_gate._check_rate_limit("1.2.3.4")
    assert allowed is False
    assert retry_after > 0
    assert retry_after <= beta_gate.RATE_LIMIT_WINDOW + 1


def test_rate_limit_is_per_ip():
    """Eine IP über dem Limit darf eine andere IP nicht blockieren."""
    for _ in range(beta_gate.RATE_LIMIT_MAX):
        beta_gate._check_rate_limit("1.2.3.4")
    # 1.2.3.4 ist nun geblockt …
    allowed_blocked, _ = beta_gate._check_rate_limit("1.2.3.4")
    assert allowed_blocked is False
    # … aber 5.6.7.8 ist davon unbetroffen.
    allowed_fresh, _ = beta_gate._check_rate_limit("5.6.7.8")
    assert allowed_fresh is True


def test_rate_limit_constants_are_pinned():
    """Lock: 10 Versuche / 60 Sekunden. Eine Erhöhung auf 100
    wäre eine stille Aushebelung des Brute-Force-Schutzes."""
    assert beta_gate.RATE_LIMIT_MAX == 10
    assert beta_gate.RATE_LIMIT_WINDOW == 60
