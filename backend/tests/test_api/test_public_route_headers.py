"""Security-Header auf der ganzen öffentlichen Strecke.

Prüft die echte App (``create_app``) mit einem nachgebauten Frontend-
Build: Startseite ``/`` (SPA), ein Landing-Asset unter ``/landing/``,
die Warteliste-API und die Health-Route müssen alle HSTS, CSP,
X-Frame-Options, nosniff und Referrer-Policy tragen. Die CSP darf keine
Fremd-Origins erlauben (die Landing lädt alles vom eigenen Origin).
Ohne ``with`` läuft der Lifespan nicht, es braucht also keine DB.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod

EXPECTED = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    (tmp_path / "landing" / "rooms").mkdir(parents=True)
    (tmp_path / "landing" / "rooms" / "001.webp").write_bytes(b"RIFF0000WEBP")
    (tmp_path / "index.html").write_text("<!doctype html><title>BauLV</title>")
    monkeypatch.setattr(main_mod, "STATIC_DIR", tmp_path)
    return TestClient(main_mod.create_app())


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "/warteliste/bestaetigen?token=x"),
        ("GET", "/landing/rooms/001.webp"),
        ("GET", "/assets/index-abc.js"),
        ("GET", "/api/health"),
        ("POST", "/api/waitlist"),
    ],
)
def test_security_headers_on_public_route(client, method, path):
    body = {"email": "a@example.com", "company_name": "Muster", "consent": True}
    r = client.request(method, path, json=body if method == "POST" else None)
    for name, value in EXPECTED.items():
        assert r.headers.get(name) == value, f"{path}: {name}"
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self';" in csp
    assert "frame-ancestors 'none'" in csp
    assert "http" not in csp  # keine Fremd-Origin freigegeben


def test_landing_asset_path_cannot_escape_static_dir(client):
    r = client.get("/landing/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 200 and r.text.startswith("<!doctype html>")  # SPA-Fallback, keine Datei
