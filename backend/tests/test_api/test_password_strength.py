"""Tests für ``_validate_password_strength`` in ``app.api.auth``.

v24.4.1 hat den Helper eingeführt: Mindest-Länge 10, Exact-Match-
Liste der 20 häufigsten Default-Passwörter.

v24.4.1-followup ergänzt den Prefix-Check: jedes Passwort dessen
lowercased Form mit einem bekannten Bot-Probe-Präfix beginnt
(``password*``, ``qwerty*``, ``12345*`` etc.) wird abgelehnt —
unabhängig davon ob die Variation in der Exact-Liste steht.

Smoke-Test-Trigger: ``password12`` ging in v24.4.1 durch (HTTP
201 Created), weil die Exact-Liste nur ``password``, ``password1``,
``password123`` enthielt. Der Test ``test_password12_blocked``
lockt das nach.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.auth import (
    MIN_PASSWORD_LENGTH,
    _validate_password_strength,
)


# ---------------------------------------------------------------------------
# Stufe 1 — Mindest-Länge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "",                  # leer
        "a",                 # 1
        "12345",             # 5
        "abcdefghi",         # 9 — genau unter dem Limit
    ],
)
def test_too_short_passwords_are_rejected(password):
    """Alles unter ``MIN_PASSWORD_LENGTH`` (10) → 400."""
    with pytest.raises(HTTPException) as exc:
        _validate_password_strength(password)
    assert exc.value.status_code == 400
    assert "10 Zeichen" in exc.value.detail


def test_min_length_constant_is_ten():
    """Lock den Wert damit eine künftige Absenkung auf 8 sofort rot wird."""
    assert MIN_PASSWORD_LENGTH == 10


# ---------------------------------------------------------------------------
# Stufe 2 — Exact-Match gegen _COMMON_PASSWORDS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        # Exact-Treffer aus der Liste (jeweils ≥ 10 Zeichen damit
        # nicht die Länge stattdessen greift).
        "password123",
        "12345678",      # genau 8 → schon zu kurz, fängt Stufe 1
        "qwertyuiop",
        "qwerty1234",
        "asdf1234",
        "letmein123",
        "admin1234",
        "welcome123",
        "passwort123",
        "iloveyou1",     # 9 → Länge greift
    ],
)
def test_exact_common_passwords_are_rejected(password):
    """Egal welche Stufe greift — alle diese müssen 400 erzeugen."""
    with pytest.raises(HTTPException) as exc:
        _validate_password_strength(password)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Stufe 3 — Prefix-Match (v24.4.1-followup)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        # Smoke-Test-Trigger — DIESER String ging in v24.4.1 durch.
        "password12",
        # Weitere password-Varianten
        "password!",
        "password2024",
        "password2024!",
        "passwortgeheim",
        # Tastatur-Walks
        "qwerty12",
        "qwerty98765",
        "qwertz12345",
        # Numerische Sequenzen
        "1234567890abc",
        "1111111111",
        "0000000000",
        # Account-Defaults
        "admin12345",
        "adminserver",
        "letmein2024",
        "welcome2024",
        # Klassische personalisierte
        "monkey1234",   # ist auch exact
        "monkeybrain",
        "dragonborn",
        "iloveyoumom",
        # Tastatur-Diagonalen
        "abc123def",
        "1q2w3e4r5t",
        "asdf1234567",
        # AT-spezifisch
        "geheim2024",
        "willkommen!",
    ],
)
def test_prefix_match_blocks_bot_variants(password):
    """Alle Passwörter die mit einem klassischen Schwach-Präfix
    anfangen werden geblockt — auch wenn sie nicht exakt in der
    Liste stehen."""
    with pytest.raises(HTTPException) as exc:
        _validate_password_strength(password)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "password",
    [
        # Case-Insensitivity-Test — Bot-Listen sind oft case-blind,
        # ein User der "PASSWORD12" tippt entgeht der Prüfung nicht.
        "PASSWORD12",
        "Password12",
        "PaSsWoRd12",
        "QWERTY1234567",
        "AdMiN1234",
    ],
)
def test_prefix_match_is_case_insensitive(password):
    with pytest.raises(HTTPException) as exc:
        _validate_password_strength(password)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Gute Passwörter — sollten durchgehen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        # Bewusst gewählte, nicht-präfix-beginnende Passwörter.
        # Diese sollten KEINEN Fehler erzeugen.
        "MeinSicheresPW",        # 14 Zeichen, kein Präfix-Match
        "Bauplan-Eiche-2024!",
        "kKwxlMqzy7Tn",         # zufälliger 12er
        "ich-mag-grosse-treppen",
        "Schwarzbrot42Liebe",
        "SimpleButLong123",      # startet nicht mit "simple" → OK
    ],
)
def test_strong_passwords_pass(password):
    """Smoke-Anker: Passwörter die genug Länge haben und nicht mit
    einem Schwach-Präfix beginnen müssen durchgelassen werden.
    Falls einer dieser Tests rot wird, hat die Präfix-Liste einen
    False-Positive — bitte Liste anpassen statt Test."""
    # No exception expected.
    _validate_password_strength(password)
