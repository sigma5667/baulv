"""Server-Logs enthalten keine E-Mail-Adressen im Klartext (Datenminimierung).

Pinnt:
  * ``email_log_hash`` ist stabil, normalisiert und enthält kein '@'.
  * Derselbe Hash wie im Audit-Log (``_email_audit_hash``).
  * Die Mail-Funktionen loggen nur den Hash — geprüft im Pfad ohne
    RESEND_API_KEY, der in Tests ohnehin aktiv ist.
  * Quelltext-Pin: kein Logger-Aufruf in ``app/`` übergibt eine
    E-Mail-Variable ungehasht.
"""

from __future__ import annotations

import ast
import logging
import pathlib

from app.api.auth import _email_audit_hash
from app.config import settings
from app.privacy import email_hash, email_log_hash
from app.services import email as email_service

ADDRESS = "Max.Muster@Example.at"


def test_log_hash_is_stable_normalised_and_has_no_address():
    h = email_log_hash(ADDRESS)
    assert h == email_log_hash("  max.muster@example.at ")
    assert h.startswith("mail#") and len(h) == len("mail#") + 12
    assert "@" not in h and "muster" not in h.lower()
    assert email_log_hash(None) == "mail#-"


def test_same_hash_space_as_audit_log():
    assert _email_audit_hash("a@b.at") == email_hash("a@b.at")
    assert email_log_hash("a@b.at")[5:] == email_hash("a@b.at")[:12]


def test_mail_functions_log_only_hash(monkeypatch, caplog):
    monkeypatch.setattr(settings, "resend_api_key", "")
    caplog.set_level(logging.DEBUG)

    email_service.send_password_reset_email(to_email=ADDRESS, reset_token="t")
    email_service.send_email_verification_email(to_email=ADDRESS, verify_token="t")

    text = caplog.text.lower()
    assert "max.muster" not in text
    assert "example.at" not in text
    assert email_log_hash(ADDRESS) in caplog.text


def test_no_logger_call_passes_plain_email():
    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {"debug", "info", "warning", "error", "exception", "critical"}
                and isinstance(node.func.value, ast.Name)
                and "log" in node.func.value.id
            ):
                continue
            for arg in node.args[1:]:
                src = ast.unparse(arg)
                if "email" in src.lower() and "email_log_hash" not in src:
                    offenders.append(f"{path.name}:{node.lineno}: {src}")
    assert offenders == []
