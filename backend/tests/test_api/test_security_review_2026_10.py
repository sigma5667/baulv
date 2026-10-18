"""Regression tests for the security review 2026-10.

One block per fix:

  1. MCP sessions are bound to the credential that opened them — a
     different (valid) credential cannot post into someone else's
     SSE session.
  2. Admin rights come only from ``users.is_admin``; an email on
     ``ADMIN_EMAILS`` alone no longer grants anything.
  3. User-supplied names are HTML-escaped in transactional emails.
  4. Password reset and password change revoke every API key.
  5. MCP write tools require the ``api_write_tools`` plan feature.
  6. XLSX exports never contain formula cells.
"""

from __future__ import annotations

import io
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import anyio
import pytest
from fastapi import HTTPException
from mcp.server.auth.middleware.bearer_auth import authorization_context
from mcp.server.sse import SseServerTransport
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.api.auth import _is_admin, change_password, confirm_password_reset
from app.auth import hash_password
from app.config import settings
from app.db.models.api_key import ApiKey
from app.db.models.session import UserSession
from app.db.models.user import User
from app.export.xlsx_exporter import _neutralize_formulas
from app.legal_versions import PRIVACY_POLICY_VERSION, TERMS_VERSION
from app.mcp import server as server_module
from app.mcp import transport as transport_module
from app.mcp.principal import Principal, current_user_id_var
from app.schemas.user import PasswordChangeRequest, PasswordResetConfirm
from app.services import email as email_module
from app.services.password_reset import mint_reset_token


def _mock_request():
    request = MagicMock()
    request.headers = {}
    request.client = None
    return request


async def _seed_user(
    db: AsyncSession, *, email: str, plan: str = "basis"
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password("originalpass123"),
        full_name="Test User",
        company_name=None,
        marketing_email_opt_in=False,
        current_privacy_version=PRIVACY_POLICY_VERSION,
        current_terms_version=TERMS_VERSION,
        subscription_plan=plan,
    )
    db.add(user)
    await db.commit()
    return user


async def _seed_api_key(db: AsyncSession, user: User) -> ApiKey:
    key = ApiKey(
        user_id=user.id,
        name="n8n",
        key_prefix="pat_test",
        key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    db.add(key)
    await db.commit()
    return key


# ---------------------------------------------------------------------------
# 1. MCP session binding
# ---------------------------------------------------------------------------


def _principal(*, with_key: bool = True) -> Principal:
    user = User(id=uuid.uuid4(), email="x@example.at", password_hash="x", full_name="X")
    api_key = ApiKey(id=uuid.uuid4(), user_id=user.id) if with_key else None
    return Principal(user=user, api_key=api_key)


async def _post_into_session(
    sse: SseServerTransport, session_id: uuid.UUID, caller: Principal
) -> int:
    """Drive the wrapped POST handler as ``caller`` and return the
    HTTP status the SDK answers with."""
    app = transport_module._wrap_messages_with_auth(sse.handle_post_message)
    body = json.dumps({"jsonrpc": "2.0", "method": "ping", "id": 1}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": f"session_id={session_id.hex}".encode(),
        "headers": [(b"content-type", b"application/json")],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def _auth(_scope):
        return caller

    with patch.object(transport_module, "_authenticate_scope", _auth):
        await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _open_session(sse: SseServerTransport, owner: Principal) -> uuid.UUID:
    """Register a session the way ``connect_sse`` does: the owner's
    authorization context is taken from ``scope["user"]``."""
    owner_scope: dict = {}
    transport_module._bind_principal_to_scope(owner_scope, owner)
    session_id = uuid.uuid4()
    writer, _reader = anyio.create_memory_object_stream(10)
    sse._read_stream_writers[session_id] = writer
    sse._session_owners[session_id] = authorization_context(owner_scope["user"])
    return session_id


async def test_mcp_post_from_other_user_is_rejected():
    sse = SseServerTransport("/mcp/messages/")
    victim, attacker = _principal(), _principal()
    session_id = _open_session(sse, victim)

    assert await _post_into_session(sse, session_id, attacker) == 404


async def test_mcp_post_with_other_key_of_same_user_is_rejected():
    """A second PAT of the same user must not drive a session opened
    by the first one — otherwise revoking a leaked key and minting a
    new one would not cut the leaked session off."""
    sse = SseServerTransport("/mcp/messages/")
    owner = _principal()
    other_key = Principal(
        user=owner.user, api_key=ApiKey(id=uuid.uuid4(), user_id=owner.user.id)
    )
    session_id = _open_session(sse, owner)

    assert await _post_into_session(sse, session_id, other_key) == 404


async def test_mcp_post_from_session_owner_is_accepted():
    sse = SseServerTransport("/mcp/messages/")
    owner = _principal()
    session_id = _open_session(sse, owner)

    assert await _post_into_session(sse, session_id, owner) == 202


async def test_mcp_jwt_session_is_bound_to_user():
    sse = SseServerTransport("/mcp/messages/")
    owner = _principal(with_key=False)
    session_id = _open_session(sse, owner)

    assert await _post_into_session(sse, session_id, _principal(with_key=False)) == 404
    assert await _post_into_session(sse, session_id, owner) == 202


# ---------------------------------------------------------------------------
# 2. Admin gate
# ---------------------------------------------------------------------------


async def test_allowlisted_email_without_flag_is_not_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "tobi@baulv.at")
    user = User(
        id=uuid.uuid4(), email="tobi@baulv.at", password_hash="x",
        full_name="X", is_admin=False,
    )
    assert _is_admin(user) is False
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(user)
    assert exc_info.value.status_code == 403


async def test_admin_flag_grants_access():
    user = User(
        id=uuid.uuid4(), email="wer@example.at", password_hash="x",
        full_name="X", is_admin=True,
    )
    assert _is_admin(user) is True
    assert await require_admin(user) is user


# ---------------------------------------------------------------------------
# 3. HTML escaping in emails
# ---------------------------------------------------------------------------

_EVIL_NAME = '<a href="https://evil.tld">Konto verifizieren</a><div style="display:none">'


def _capture_send(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _fake_send(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(email_module, "_send", _fake_send)
    return calls


def test_password_reset_email_escapes_name(monkeypatch):
    calls = _capture_send(monkeypatch)
    email_module.send_password_reset_email(
        to_email="opfer@example.at", reset_token="t", user_name=_EVIL_NAME
    )
    html_body = calls[0]["html_body"]
    assert "evil.tld" in html_body  # the text is still there …
    assert '<a href="https://evil.tld">' not in html_body  # … but inert
    assert '<div style="display:none">' not in html_body


def test_waitlist_confirm_email_escapes_name(monkeypatch):
    calls = _capture_send(monkeypatch)
    email_module.send_waitlist_confirm_email(
        to_email="opfer@example.at",
        confirm_token="c",
        unsubscribe_token="u",
        name=_EVIL_NAME,
    )
    assert '<a href="https://evil.tld">' not in calls[0]["html_body"]


# ---------------------------------------------------------------------------
# 4. Password reset / change revoke API keys
# ---------------------------------------------------------------------------


async def test_password_reset_revokes_api_keys(db_session: AsyncSession):
    user = await _seed_user(db_session, email="reset-pat@example.com")
    key = await _seed_api_key(db_session, user)

    plaintext = await mint_reset_token(db_session, user=user)
    await db_session.commit()
    await confirm_password_reset(
        PasswordResetConfirm(token=plaintext, new_password="newSecret123"),
        _mock_request(),
        db=db_session,
    )
    await db_session.commit()

    await db_session.refresh(key)
    assert key.revoked_at is not None


async def test_password_change_revokes_api_keys(db_session: AsyncSession):
    user = await _seed_user(db_session, email="change-pat@example.com")
    key = await _seed_api_key(db_session, user)
    current = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        jti="current-jti",
        user_agent=None,
        ip_address=None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(current)
    await db_session.commit()

    await change_password(
        PasswordChangeRequest(
            current_password="originalpass123", new_password="newSecret123"
        ),
        _mock_request(),
        user=user,
        current_session=current,
        db=db_session,
    )
    await db_session.commit()

    await db_session.refresh(key)
    await db_session.refresh(current)
    assert key.revoked_at is not None
    assert current.revoked_at is None  # the caller stays logged in


# ---------------------------------------------------------------------------
# 5. MCP write tools need the plan feature
# ---------------------------------------------------------------------------


async def _call_tool_as(monkeypatch, db_session, user, name, arguments):
    @asynccontextmanager
    async def _factory():
        yield db_session

    async def _no_audit(**_kwargs):
        return None

    monkeypatch.setattr(server_module, "async_session_factory", _factory)
    monkeypatch.setattr(server_module, "write_audit_entry", _no_audit)
    token = current_user_id_var.set(user.id)
    try:
        return await server_module.call_tool(name, arguments)
    finally:
        current_user_id_var.reset(token)


async def test_basis_user_cannot_use_write_tools(monkeypatch, db_session):
    monkeypatch.setattr(settings, "beta_unlock_all_features", False)
    user = await _seed_user(db_session, email="free@example.com", plan="basis")

    result = await _call_tool_as(
        monkeypatch, db_session, user, "create_project", {"name": "Neu"}
    )
    assert "Pro-Plan" in result[0].text


async def test_basis_user_can_use_read_tools(monkeypatch, db_session):
    user = await _seed_user(db_session, email="free-read@example.com", plan="basis")

    result = await _call_tool_as(monkeypatch, db_session, user, "list_projects", {})
    assert "Pro-Plan" not in result[0].text


async def test_pro_user_can_use_write_tools(monkeypatch, db_session):
    user = await _seed_user(db_session, email="pro@example.com", plan="pro")

    result = await _call_tool_as(
        monkeypatch, db_session, user, "create_project", {"name": "Neu"}
    )
    assert "Pro-Plan" not in result[0].text
    assert "Neu" in result[0].text


# ---------------------------------------------------------------------------
# 6. XLSX formula neutralisation
# ---------------------------------------------------------------------------


def test_xlsx_cells_starting_with_equals_are_stored_as_text():
    wb = Workbook()
    wb.active["A1"] = '=HYPERLINK("https://evil.tld","Klick")'
    _neutralize_formulas(wb)

    buffer = io.BytesIO()
    wb.save(buffer)
    import zipfile

    sheet_xml = zipfile.ZipFile(io.BytesIO(buffer.getvalue())).read(
        "xl/worksheets/sheet1.xml"
    )
    assert b"<f>" not in sheet_xml
    assert b"HYPERLINK" in sheet_xml


# ---------------------------------------------------------------------------
# 7. Migration 029 — upgrade promotes, downgrade reverts exactly that
# ---------------------------------------------------------------------------


def _load_migration_029():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "029_admin_flag_from_allowlist.py"
    )
    spec = importlib.util.spec_from_file_location("migration_029", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_029_upgrade_and_downgrade(monkeypatch):
    import sqlalchemy as sa

    m029 = _load_migration_029()
    monkeypatch.setattr(settings, "admin_emails", " Tobi@BauLV.at , nie-registriert@x.at")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id CHAR(36), email TEXT, is_admin BOOLEAN)"))
        conn.execute(sa.text(
            "INSERT INTO users VALUES "
            "('u-listed', 'tobi@baulv.at', 0), "
            "('u-already', 'alt-admin@x.at', 1), "
            "('u-other', 'kunde@x.at', 0)"
        ))
        monkeypatch.setattr(m029.op, "get_bind", lambda: conn)

        def flags():
            return dict(conn.execute(sa.text("SELECT id, is_admin FROM users")).all())

        m029.upgrade()
        m029.upgrade()  # idempotent
        assert flags() == {"u-listed": 1, "u-already": 1, "u-other": 0}

        m029.downgrade()
        # Only the account 029 promoted goes back; the pre-existing
        # admin keeps the flag, nothing else changes.
        assert flags() == {"u-listed": 0, "u-already": 1, "u-other": 0}
        assert not sa.inspect(conn).has_table("migration_029_promoted")
