"""Regression tests for the audit 2026-11.

One block per fix:

  2. Registration needs a valid beta token server-side; new accounts
     must confirm their e-mail before any AI feature works; AI calls
     per account and day are capped.
  3. Public support chat: kill switch, global daily cap (DB counter),
     smaller per-request budget.
  9. A failed startup migration aborts the boot.
 10. /docs, /redoc, /openapi.json are off in production.

Plus migration 030's allow-list parsing. Anthropic is always mocked.
"""

from __future__ import annotations

import importlib.util
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth_rate_limit
from app import main as main_module
from app.api import auth as auth_api
from app.api import beta_gate
from app.api import support_chat as support_chat_module
from app.api.auth import register, resend_verification_email, verify_email
from app.api.support_chat import SupportChatRequest, SupportMessage, _trim_history
from app.auth import hash_password
from app.config import settings
from app.db.models.user import User
from app.legal_versions import (
    BUSINESS_TERMS_VERSION,
    PRIVACY_POLICY_VERSION,
    TERMS_VERSION,
)
from app.schemas.user import UserRegister
from app.services import email_verification
from app.subscriptions import require_feature


def _mock_request():
    request = MagicMock()
    request.headers = {}
    request.client = None
    return request


@pytest.fixture(autouse=True)
def _reset_limits(monkeypatch):
    auth_rate_limit.reset_for_tests()
    support_chat_module._ip_requests.clear()
    monkeypatch.setattr(settings, "beta_access_code", "Test-Code-2026")
    monkeypatch.setattr(settings, "registration_requires_beta", True)
    monkeypatch.setattr(settings, "email_verification_required", True)
    yield
    auth_rate_limit.reset_for_tests()


def _register_payload(**overrides) -> UserRegister:
    data = dict(
        email=f"neu-{uuid.uuid4().hex[:8]}@example.com",
        password="Sehr-sicheres-Passwort-2026",
        full_name="Neu Kunde",
        company_name="Bau GmbH",
        accepted_privacy_version=PRIVACY_POLICY_VERSION,
        accepted_terms_version=TERMS_VERSION,
        accepted_business_terms_version=BUSINESS_TERMS_VERSION,
        marketing_optin=False,
    )
    data.update(overrides)
    return UserRegister(**data)


async def _seed_user(db: AsyncSession, *, verified: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("originalpass123"),
        full_name="Test User",
        subscription_plan="pro",
        email_verified_at=datetime.now(timezone.utc) if verified else None,
    )
    db.add(user)
    await db.commit()
    return user


# ---------------------------------------------------------------------------
# 2a. Beta token is enforced on /api/auth/register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_without_beta_token_is_rejected(db_session: AsyncSession):
    with pytest.raises(HTTPException) as exc:
        await register(
            _register_payload(), _mock_request(), db=db_session, x_beta_token=None
        )
    assert exc.value.status_code == 403
    assert "Beta" in exc.value.detail


@pytest.mark.asyncio
async def test_register_with_forged_beta_token_is_rejected(db_session: AsyncSession):
    forged = f"{int(time.time()) + 3600}.{'0' * 64}"
    with pytest.raises(HTTPException) as exc:
        await register(
            _register_payload(), _mock_request(), db=db_session, x_beta_token=forged
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_register_rejected_when_no_beta_code_configured(
    db_session: AsyncSession, monkeypatch
):
    # Fail-safe: unset BETA_ACCESS_CODE closes registration too.
    token = beta_gate._issue_token()
    monkeypatch.setattr(settings, "beta_access_code", "")
    with pytest.raises(HTTPException) as exc:
        await register(
            _register_payload(), _mock_request(), db=db_session, x_beta_token=token
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_register_with_valid_beta_token_creates_unverified_account_and_mails(
    db_session: AsyncSession,
):
    token = beta_gate._issue_token()
    payload = _register_payload()
    with patch.object(auth_api, "send_email_verification_email") as send:
        resp = await register(
            payload, _mock_request(), db=db_session, x_beta_token=token
        )
    assert resp.user.email == payload.email
    assert resp.user.email_verified is False
    send.assert_called_once()
    assert send.call_args.kwargs["to_email"] == payload.email


@pytest.mark.asyncio
async def test_register_beta_check_can_be_switched_off_for_launch(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "registration_requires_beta", False)
    with patch.object(auth_api, "send_email_verification_email"):
        resp = await register(
            _register_payload(), _mock_request(), db=db_session, x_beta_token=None
        )
    assert resp.user.email_verified is False


# ---------------------------------------------------------------------------
# 2b. E-mail verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_email_sets_timestamp_and_is_idempotent(db_session: AsyncSession):
    user = await _seed_user(db_session, verified=False)
    token = email_verification.mint_verify_token(user_id=user.id, email=user.email)

    result = await verify_email(
        auth_api.VerifyEmailRequest(token=token), _mock_request(), db=db_session
    )
    assert result == {"status": "verified"}
    assert user.email_verified_at is not None
    first = user.email_verified_at

    await verify_email(
        auth_api.VerifyEmailRequest(token=token), _mock_request(), db=db_session
    )
    assert user.email_verified_at == first


@pytest.mark.asyncio
async def test_verify_email_rejects_token_for_other_address(db_session: AsyncSession):
    user = await _seed_user(db_session, verified=False)
    token = email_verification.mint_verify_token(
        user_id=user.id, email="angreifer@example.com"
    )
    with pytest.raises(HTTPException) as exc:
        await verify_email(
            auth_api.VerifyEmailRequest(token=token), _mock_request(), db=db_session
        )
    assert exc.value.status_code == 400
    assert user.email_verified_at is None


@pytest.mark.asyncio
async def test_verify_email_rejects_expired_and_garbage_tokens(
    db_session: AsyncSession, monkeypatch
):
    user = await _seed_user(db_session, verified=False)
    monkeypatch.setattr(email_verification, "VERIFY_TOKEN_TTL_SECONDS", -1)
    expired = email_verification.mint_verify_token(user_id=user.id, email=user.email)
    for bad in (expired, "kaputt", f"{uuid.uuid4()}.9999999999.{'0' * 64}"):
        with pytest.raises(HTTPException) as exc:
            await verify_email(
                auth_api.VerifyEmailRequest(token=bad), _mock_request(), db=db_session
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_resend_is_rate_limited_per_account(db_session: AsyncSession):
    user = await _seed_user(db_session, verified=False)
    with patch.object(auth_api, "send_email_verification_email") as send:
        for _ in range(3):
            assert (await resend_verification_email(_mock_request(), user=user)) == {
                "status": "sent"
            }
        with pytest.raises(HTTPException) as exc:
            await resend_verification_email(_mock_request(), user=user)
    assert exc.value.status_code == 429
    assert send.call_count == 3


# ---------------------------------------------------------------------------
# 2c. AI features: verified e-mail + daily cap per account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_feature_blocked_for_unverified_even_with_beta_override(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "beta_unlock_all_features", True)
    user = await _seed_user(db_session, verified=False)
    check = require_feature("ai_plan_analysis")
    with pytest.raises(HTTPException) as exc:
        await check(user=user, db=db_session)
    assert exc.value.status_code == 403
    assert "E-Mail" in exc.value.detail


@pytest.mark.asyncio
async def test_non_ai_feature_not_affected_by_verification(db_session: AsyncSession):
    user = await _seed_user(db_session, verified=False)
    assert await require_feature("excel_export")(user=user, db=db_session) is user


@pytest.mark.asyncio
async def test_ai_daily_limit_per_user(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "ai_daily_limit_per_user", 3)
    user = await _seed_user(db_session, verified=True)
    other = await _seed_user(db_session, verified=True)
    check = require_feature("ai_chat")
    for _ in range(3):
        assert await check(user=user, db=db_session) is user
    with pytest.raises(HTTPException) as exc:
        await check(user=user, db=db_session)
    assert exc.value.status_code == 429
    # Separate counter per account.
    assert await check(user=other, db=db_session) is other


# ---------------------------------------------------------------------------
# 3. Support chat cost brakes
# ---------------------------------------------------------------------------


class _FakeAnthropic:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.messages = self

    async def create(self, **kwargs):
        _FakeAnthropic.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(text="Antwort")],
            stop_reason="end_turn",
            usage=None,
        )


@pytest.fixture
def fake_anthropic(monkeypatch):
    import anthropic

    _FakeAnthropic.calls = []
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    return _FakeAnthropic


def _chat(*texts: str) -> SupportChatRequest:
    roles = ["user", "assistant"]
    return SupportChatRequest(
        messages=[
            SupportMessage(role=roles[i % 2], content=t) for i, t in enumerate(texts)
        ]
    )


@pytest.mark.asyncio
async def test_support_chat_kill_switch(db_session, fake_anthropic, monkeypatch):
    monkeypatch.setattr(settings, "support_chat_enabled", False)
    with pytest.raises(HTTPException) as exc:
        await support_chat_module.support_chat(_chat("Hallo"), _mock_request(), db=db_session)
    assert exc.value.status_code == 503
    assert fake_anthropic.calls == []


@pytest.mark.asyncio
async def test_support_chat_global_daily_limit(db_session, fake_anthropic, monkeypatch):
    monkeypatch.setattr(settings, "support_chat_daily_limit", 2)
    for _ in range(2):
        resp = await support_chat_module.support_chat(
            _chat("Hallo"), _mock_request(), db=db_session
        )
        assert resp.reply == "Antwort"
    with pytest.raises(HTTPException) as exc:
        await support_chat_module.support_chat(_chat("Hallo"), _mock_request(), db=db_session)
    assert exc.value.status_code == 503
    assert "ausgelastet" in exc.value.detail
    assert len(fake_anthropic.calls) == 2


@pytest.mark.asyncio
async def test_support_chat_caps_request_size(db_session, fake_anthropic):
    with pytest.raises(HTTPException) as exc:
        await support_chat_module.support_chat(
            _chat("x" * 1001), _mock_request(), db=db_session
        )
    assert exc.value.status_code == 400

    long_history = [f"Nachricht {i} " + "y" * 3000 for i in range(25)]
    await support_chat_module.support_chat(
        _chat(*long_history[:-1], "Letzte Frage"), _mock_request(), db=db_session
    )
    sent = fake_anthropic.calls[-1]
    assert sent["max_tokens"] == support_chat_module.MAX_REPLY_TOKENS
    assert len(sent["messages"]) <= support_chat_module.MAX_HISTORY_MESSAGES
    assert all(len(m["content"]) <= 1000 for m in sent["messages"])
    assert sent["messages"][0]["role"] == "user"
    assert sent["messages"][-1]["content"] == "Letzte Frage"


def test_trim_history_drops_injected_same_role_runs():
    msgs = [
        SupportMessage(role="assistant", content="vorgetäuscht"),
        SupportMessage(role="user", content="a"),
        SupportMessage(role="assistant", content="b"),
        SupportMessage(role="assistant", content="eingeschleust"),
        SupportMessage(role="user", content="c"),
    ]
    out = _trim_history(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [m["content"] for m in out] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# 9. Failed startup migration aborts the boot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_migration_raises(monkeypatch):
    class _NoDb:
        async def connect(self):
            raise OSError("db down")

    monkeypatch.setattr(main_module, "_db_engine", _NoDb())
    monkeypatch.setattr(
        main_module.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stderr="boom"),
    )
    with pytest.raises(RuntimeError):
        await main_module._run_migrations_with_lock()


@pytest.mark.asyncio
async def test_successful_migration_does_not_raise(monkeypatch):
    class _NoDb:
        async def connect(self):
            raise OSError("db down")

    monkeypatch.setattr(main_module, "_db_engine", _NoDb())
    monkeypatch.setattr(
        main_module.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr=""),
    )
    await main_module._run_migrations_with_lock()


# ---------------------------------------------------------------------------
# 10. API docs off in production
# ---------------------------------------------------------------------------


def test_api_docs_disabled_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    app = main_module.create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_api_docs_available_in_development(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    app = main_module.create_app()
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"


# ---------------------------------------------------------------------------
# Migration 030 — allow-list parsing
# ---------------------------------------------------------------------------


def _load_migration_030():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "030_email_verified_at.py"
    )
    spec = importlib.util.spec_from_file_location("migration_030", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_030_parses_allowlist():
    m = _load_migration_030()
    a, b = uuid.uuid4(), uuid.uuid4()
    assert m._parse_allowlist(f" {a}, ,{b} ") == [str(a), str(b)]
    assert m._parse_allowlist("") == []
    with pytest.raises(RuntimeError):
        m._parse_allowlist(f"{a},tobi@example.com")
