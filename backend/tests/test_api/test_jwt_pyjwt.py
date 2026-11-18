"""JWT-Umstellung python-jose → PyJWT (2026-11-18).

python-jose ist ungepflegt und zog ecdsa/rsa/pyasn1 mit offenen
Lücken nach. Diese Tests sichern, dass der Wechsel für Nutzer
unsichtbar bleibt und die Prüfung nicht weicher geworden ist:

1. Ein Token, das noch mit python-jose ausgestellt wurde, ist mit
   PyJWT weiter gültig (eingeloggte Nutzer bleiben eingeloggt).
2. Manipulierte, abgelaufene, fremd signierte und ``alg: none``-Tokens
   werden abgelehnt.
3. Durchgang Registrierung → Login → /me → Logout → /me = 401.
4. passlib/jose/ecdsa werden von der App nirgends mehr importiert.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth as auth_mod
from app.api.auth import get_me, login, register, revoke_my_session
from app.auth import (
    create_access_token,
    decode_token,
    get_current_session,
    get_current_user,
)
from app.config import settings
from app.legal_versions import (
    BUSINESS_TERMS_VERSION,
    PRIVACY_POLICY_VERSION,
    TERMS_VERSION,
)
from app.schemas.user import UserLogin, UserRegister

# Mit python-jose 3.5.0 erzeugt (HS256, exp = 01.01.2100):
#   jose.jwt.encode({"sub": "00000000-0000-0000-0000-000000000042",
#                    "exp": 4102444800, "iat": 1763400000,
#                    "jti": "legacy-jose-jti"}, _JOSE_SECRET, "HS256")
_JOSE_SECRET = "test-secret-for-jose-compat-0123456789abcdef"
_JOSE_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwNDIiLCJleHAiOjQx"
    "MDI0NDQ4MDAsImlhdCI6MTc2MzQwMDAwMCwianRpIjoibGVnYWN5LWpvc2UtanRpIn0."
    "_aIWbbtaNMx9JYzh2q1etNqP6NOJMBqXcsiOX251Ybk"
)


def _b64(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# 1 + 2 — Token-Ebene
# ---------------------------------------------------------------------------


def test_token_from_python_jose_still_valid(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", _JOSE_SECRET)
    payload = decode_token(_JOSE_TOKEN)
    assert payload is not None
    assert payload["sub"] == "00000000-0000-0000-0000-000000000042"
    assert payload["jti"] == "legacy-jose-jti"


def test_roundtrip_returns_claims():
    uid = uuid.uuid4()
    token, jti, _ = create_access_token(uid)
    payload = decode_token(token)
    assert payload["sub"] == str(uid)
    assert payload["jti"] == jti
    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "HS256"


def test_tampered_payload_rejected():
    token, _, _ = create_access_token(uuid.uuid4())
    head, _, sig = token.split(".")
    forged = _b64({"sub": str(uuid.uuid4()), "jti": "x", "exp": 4102444800})
    assert decode_token(f"{head}.{forged}.{sig}") is None


def test_wrong_secret_rejected():
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "jti": "x", "exp": 4102444800},
        "ein-ganz-anderer-schluessel-0123456789abcdef",
        algorithm="HS256",
    )
    assert decode_token(token) is None


def test_expired_token_rejected():
    token, _, _ = create_access_token(
        uuid.uuid4(), expires_delta=timedelta(seconds=-5)
    )
    assert decode_token(token) is None


def test_alg_none_rejected():
    token = (
        _b64({"alg": "none", "typ": "JWT"})
        + "."
        + _b64({"sub": str(uuid.uuid4()), "jti": "x", "exp": 4102444800})
        + "."
    )
    assert decode_token(token) is None


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
def test_other_hmac_alg_rejected():
    """Nur HS256 ist erlaubt — ein HS512-Token mit dem richtigen
    Schlüssel wird trotzdem abgelehnt."""
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "jti": "x", "exp": 4102444800},
        settings.jwt_secret,
        algorithm="HS512",
    )
    assert decode_token(token) is None


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x.y.z"])
def test_garbage_returns_none_not_exception(garbage):
    assert decode_token(garbage) is None


# ---------------------------------------------------------------------------
# 3 — Registrierung → Login → /me → Logout
# ---------------------------------------------------------------------------


def _request():
    request = MagicMock()
    request.headers = {"user-agent": "pytest", "x-forwarded-for": "203.0.113.7"}
    request.client = None
    return request


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_register_login_me_logout_flow(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "registration_requires_beta", False)
    email = f"jwt-{uuid.uuid4()}@example.com"
    password = "Sehr-Sicheres-Passwort-2026"

    reg = await register(
        UserRegister(
            email=email,
            password=password,
            full_name="JWT Test",
            company_name="Test GmbH",
            accepted_privacy_version=PRIVACY_POLICY_VERSION,
            accepted_terms_version=TERMS_VERSION,
            accepted_business_terms_version=BUSINESS_TERMS_VERSION,
        ),
        _request(),
        db=db_session,
    )
    await db_session.commit()
    assert decode_token(reg.access_token) is not None

    # Falsches Passwort → 401
    with pytest.raises(HTTPException) as exc:
        await login(UserLogin(email=email, password="falsch-falsch-1"), _request(), db=db_session)
    assert exc.value.status_code == 401

    # Login → Token → /me
    res = await login(UserLogin(email=email, password=password), _request(), db=db_session)
    await db_session.commit()
    user = await get_current_user(_creds(res.access_token), db=db_session)
    me = await get_me(user=user)
    assert me.email == email

    # Logout = aktuelle Sitzung widerrufen
    session_row = await get_current_session(_creds(res.access_token), db=db_session)
    await revoke_my_session(
        session_row.id,
        _request(),
        user=user,
        current_session=session_row,
        db=db_session,
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(res.access_token), db=db_session)
    assert exc.value.status_code == 401

    # Das Registrierungs-Token (andere Sitzung) gilt weiter
    still = await get_current_user(_creds(reg.access_token), db=db_session)
    assert still.id == user.id


# ---------------------------------------------------------------------------
# 4 — Alte Bibliotheken werden nicht mehr importiert
# ---------------------------------------------------------------------------


def test_auth_module_uses_pyjwt():
    assert auth_mod.jwt is pyjwt


def test_app_imports_without_jose_passlib_ecdsa():
    """Lädt die komplette App in einem frischen Interpreter, in dem
    ``jose``, ``passlib`` und ``ecdsa`` blockiert sind."""
    backend = Path(__file__).resolve().parents[2]
    code = (
        "import sys\n"
        "for m in ('jose', 'passlib', 'ecdsa'):\n"
        "    sys.modules[m] = None\n"
        "import app.main\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "ENVIRONMENT": "test"},
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("ok")
