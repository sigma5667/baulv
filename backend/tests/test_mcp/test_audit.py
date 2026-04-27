"""Unit tests for the MCP audit-log writer + sanitizer.

We don't drive the SSE wire here. The aim is to lock down two
behaviours that are easy to break in subtle ways:

1. ``sanitize_arguments`` clips oversized values so a malformed agent
   call can't blow up audit storage.
2. ``write_audit_entry`` is best-effort — the row reaches the DB on
   the happy path, and a failure inside the writer must not propagate
   up into the caller.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mcp_audit import (
    RESULT_ERROR,
    RESULT_OK,
    RESULT_RATE_LIMITED,
    McpAuditLogEntry,
)
from app.mcp.audit import sanitize_arguments


# ---------------------------------------------------------------------------
# sanitize_arguments — pure-function tests
# ---------------------------------------------------------------------------


def test_sanitize_returns_none_for_empty():
    """Empty dict / None → None. We don't pollute the audit table with
    ``{}`` rows that carry no information."""
    assert sanitize_arguments(None) is None
    assert sanitize_arguments({}) is None


def test_sanitize_passes_through_short_values():
    """Tiny inputs round-trip unchanged — a passing audit row should
    be readable in the frontend exactly as the agent sent it."""
    args = {"project_id": "abc", "name": "Demo", "count": 3, "active": True}
    out = sanitize_arguments(args)
    assert out == args


def test_sanitize_clips_long_strings():
    """A string past the per-string cap must come out truncated with
    a ``…[N more chars]`` suffix so the user can see *that* it was
    clipped."""
    long = "x" * 1500
    out = sanitize_arguments({"description": long})
    assert out is not None
    text = out["description"]
    assert isinstance(text, str)
    assert text.startswith("x" * 500)
    assert text.endswith("more chars]")
    assert len(text) < len(long)


def test_sanitize_replaces_oversized_nested():
    """A list / dict that JSON-encodes past the per-value cap is
    replaced with a sentinel — keeps row size predictable."""
    huge_list = list(range(10_000))
    out = sanitize_arguments({"rows": huge_list})
    assert out is not None
    assert out["rows"] == "<too large>"


def test_sanitize_handles_unserialisable_gracefully():
    """A nested value that ``json.dumps`` rejects must produce a
    sentinel, not an exception. Defensive: an agent could in theory
    ship something exotic in the args dict and we don't want that to
    crash the audit write."""

    class NotSerialisable:
        pass

    out = sanitize_arguments({"thing": [NotSerialisable()]})
    assert out is not None
    # ``default=str`` in the encoder makes most odd things stringify;
    # but if it ever returns a TypeError the helper falls back to a
    # sentinel, which is the path we care about. Either way the
    # output is JSON-safe.
    import json

    json.dumps(out)  # must not raise


# ---------------------------------------------------------------------------
# write_audit_entry — DB-backed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_audit_entry_persists_row(monkeypatch):
    """Happy path: the writer commits one row reflecting all args.

    We give the writer its own dedicated SQLite engine (with a
    ``StaticPool`` so the in-memory DB survives across sessions) and
    monkey-patch its session factory to point at that engine. Then we
    open a verification session against the same engine to read back.

    This avoids sharing the test's ``db_session`` engine, which uses a
    pool that may not retain in-memory state across separate sessions.
    """
    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base
    from app.mcp import audit as audit_module

    # Importing ensures every model is registered on Base.metadata
    # before create_all runs.
    import app.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        # StaticPool keeps the same connection alive for every
        # checkout — without this each new session would see a fresh
        # empty in-memory database.
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(audit_module, "async_session_factory", factory)

    user_id = uuid.uuid4()
    api_key_id = uuid.uuid4()

    await audit_module.write_audit_entry(
        user_id=user_id,
        api_key_id=api_key_id,
        tool_name="list_projects",
        arguments={"foo": "bar"},
        result=RESULT_OK,
        error_message=None,
        latency_ms=42,
    )

    async with factory() as verify_session:
        rows = (
            await verify_session.execute(select(McpAuditLogEntry))
        ).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == user_id
    assert row.api_key_id == api_key_id
    assert row.tool_name == "list_projects"
    assert row.arguments == {"foo": "bar"}
    assert row.result == RESULT_OK
    assert row.error_message is None
    assert row.latency_ms == 42

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_audit_entry_swallows_failures(monkeypatch, caplog):
    """If session creation blows up, the writer must log + swallow.

    The contract: a tool's response to the agent must never depend on
    the audit write succeeding. A broken DB / disk-full / network
    blip on the audit path can't be allowed to surface as a 500."""
    from app.mcp import audit as audit_module

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated audit DB outage")

    monkeypatch.setattr(audit_module, "async_session_factory", _explode)

    # Must not raise — caller's tool result is preserved.
    await audit_module.write_audit_entry(
        user_id=uuid.uuid4(),
        api_key_id=uuid.uuid4(),
        tool_name="list_projects",
        arguments=None,
        result=RESULT_ERROR,
        error_message="boom",
        latency_ms=12,
    )


def test_result_constants_match_schema_vocabulary():
    """Lock the canonical vocabulary in one place — the frontend filter
    and the dispatcher writer both index off these strings, and a typo
    would silently produce orphaned rows nobody filters on."""
    assert RESULT_OK == "ok"
    assert RESULT_ERROR == "error"
    assert RESULT_RATE_LIMITED == "rate_limited"
