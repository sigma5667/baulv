"""Die Double-Opt-In-Mail darf keinen vom Absender frei eingegebenen
Text enthalten (Sicherheits-Check 2026-11-24).

Die Mail geht an eine noch unbestätigte Adresse, die jeder Dritte ins
Formular tippen kann. Mit dem Namen in der Anrede ließen sich Text und
Links in eine echte BauLV-Mail an Fremde schmuggeln.
"""

from __future__ import annotations

from app.services import email as email_module

_INJECT = "Ihr Konto wird gesperrt, bitte sofort evil.tld/login besuchen"


def test_confirm_mail_does_not_echo_name(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(email_module, "_send", lambda **kw: calls.append(kw) or True)
    email_module.send_waitlist_confirm_email(
        to_email="opfer@example.at", confirm_token="c", unsubscribe_token="u", name=_INJECT
    )
    assert len(calls) == 1
    mail = calls[0]
    for part in ("html_body", "text_body", "subject"):
        assert "evil.tld" not in str(mail.get(part, "")), part
    assert "Guten Tag," in mail["text_body"]
    assert "warteliste/bestaetigen?token=c" in mail["text_body"]


# ---------------------------------------------------------------------------
# Abmelde-Links überleben eine JWT-Rotation (eigenes Secret)
# ---------------------------------------------------------------------------

from app.config import settings  # noqa: E402
from app.services.waitlist import mint_unsubscribe_token, resolve_unsubscribe_token  # noqa: E402


def test_unsubscribe_link_survives_jwt_rotation(monkeypatch):
    monkeypatch.setattr(settings, "waitlist_unsubscribe_secret", "dauerhaft-geheim")
    monkeypatch.setattr(settings, "jwt_secret", "jwt-alt")
    token = mint_unsubscribe_token("kunde@example.at")
    monkeypatch.setattr(settings, "jwt_secret", "jwt-neu-nach-rotation")
    assert resolve_unsubscribe_token(token) == "kunde@example.at"


def test_unsubscribe_falls_back_to_jwt_secret_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "waitlist_unsubscribe_secret", "")
    monkeypatch.setattr(settings, "jwt_secret", "jwt-a")
    token = mint_unsubscribe_token("kunde@example.at")
    assert resolve_unsubscribe_token(token) == "kunde@example.at"
    monkeypatch.setattr(settings, "jwt_secret", "jwt-b")
    assert resolve_unsubscribe_token(token) is None  # bisheriges Verhalten, darum Secret setzen
