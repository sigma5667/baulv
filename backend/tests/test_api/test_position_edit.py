"""Tests for the v23.5 inline-edit flow on LV positions.

Coverage matches the P0 spec:

  1. ``menge`` and ``einheitspreis`` are writeable on an unlocked
     position; the response carries the new values + the recomputed
     ``gesamtpreis``.
  2. ``is_locked`` flips both ways via the same endpoint.
  3. A locked position rejects any field other than ``is_locked``
     with 409 — the user can still *unlock* directly, but every
     other write needs the unlock-first dance.
  4. Cross-tenant protection: user B cannot update user A's position
     (the existing ownership check via ``verify_lv_owner`` is the
     gate; the test verifies it actually fires for this endpoint).

Tests call the endpoint *function* directly with a mocked request,
matching the convention in other tests in this package.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lv import update_position
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.project import Project
from app.db.models.user import User
from app.schemas.lv import PositionUpdate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_position(
    db: AsyncSession,
    *,
    email_prefix: str = "u",
    is_locked: bool = False,
    menge: Decimal | None = Decimal("10.000"),
    einheitspreis: Decimal | None = Decimal("5.50"),
) -> tuple[User, Project, Position]:
    """Build the smallest chain that lets ``update_position`` run:
    User → Project → LV → Gruppe → Position.

    Returns ``(user, project, position)`` — most tests only need the
    first and the last but a few (cross-tenant) keep the project
    around to seed a *second* user against the same row."""
    user = User(
        id=uuid.uuid4(),
        email=f"{email_prefix}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test",
    )
    db.add(user)
    await db.flush()

    project = Project(id=uuid.uuid4(), user_id=user.id, name="P")
    db.add(project)
    await db.flush()

    lv = Leistungsverzeichnis(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Test LV",
        trade="malerarbeiten",
    )
    db.add(lv)
    await db.flush()

    gruppe = Leistungsgruppe(
        id=uuid.uuid4(),
        lv_id=lv.id,
        nummer="01",
        bezeichnung="Wandanstriche",
    )
    db.add(gruppe)
    await db.flush()

    position = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        positions_nummer="01.01",
        kurztext="Wand grundieren",
        langtext=None,
        einheit="m2",
        menge=menge,
        einheitspreis=einheitspreis,
        is_locked=is_locked,
    )
    db.add(position)
    await db.commit()
    return user, project, position


# ---------------------------------------------------------------------------
# 1. Happy-path edits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_position_writes_menge_and_einheitspreis(
    db_session: AsyncSession,
):
    """Menge + Einheitspreis update via PUT on an unlocked position
    persists, and the response carries the recomputed gesamtpreis."""
    user, _, position = await _seed_position(db_session)

    response = await update_position(
        position_id=position.id,
        data=PositionUpdate(menge=15.5, einheitspreis=7.25),
        user=user,
        db=db_session,
    )
    await db_session.commit()

    # Reload from DB to confirm persistence (not just a stale ORM
    # attribute on the in-memory instance).
    await db_session.refresh(position)
    assert float(position.menge) == pytest.approx(15.5)
    assert float(position.einheitspreis) == pytest.approx(7.25)
    # Computed property — 15.5 × 7.25 = 112.375.
    assert float(position.gesamtpreis or 0) == pytest.approx(112.375)
    # Endpoint return value mirrors the persisted state.
    assert float(response.menge or 0) == pytest.approx(15.5)


@pytest.mark.asyncio
async def test_update_position_locks_and_unlocks(db_session: AsyncSession):
    """Toggling ``is_locked`` round-trips through the endpoint."""
    user, _, position = await _seed_position(db_session, is_locked=False)

    await update_position(
        position_id=position.id,
        data=PositionUpdate(is_locked=True),
        user=user,
        db=db_session,
    )
    await db_session.commit()
    await db_session.refresh(position)
    assert position.is_locked is True

    # And back. The lock-gate must allow this — see test below for
    # the gate's interaction with non-lock fields.
    await update_position(
        position_id=position.id,
        data=PositionUpdate(is_locked=False),
        user=user,
        db=db_session,
    )
    await db_session.commit()
    await db_session.refresh(position)
    assert position.is_locked is False


# ---------------------------------------------------------------------------
# 2. Lock gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_position_rejects_menge_update_409(
    db_session: AsyncSession,
):
    """A position with ``is_locked=True`` must reject a
    ``menge`` update with 409. The DB row stays untouched."""
    user, _, position = await _seed_position(
        db_session, is_locked=True, menge=Decimal("10.000")
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_position(
            position_id=position.id,
            data=PositionUpdate(menge=99.0),
            user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 409
    assert "gesperrt" in exc_info.value.detail

    await db_session.rollback()
    await db_session.refresh(position)
    # Untouched.
    assert float(position.menge) == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_locked_position_allows_unlock_only_payload(
    db_session: AsyncSession,
):
    """Even though the row is locked, an ``is_locked=False`` payload
    on its own must succeed — that's how the user re-enables editing.
    Combining the unlock with another field in the same payload must
    still fail (the row is locked at the moment of the write)."""
    user, _, position = await _seed_position(db_session, is_locked=True)

    # Unlock-only — succeeds.
    await update_position(
        position_id=position.id,
        data=PositionUpdate(is_locked=False),
        user=user,
        db=db_session,
    )
    await db_session.commit()
    await db_session.refresh(position)
    assert position.is_locked is False

    # Re-lock for the next assertion.
    position.is_locked = True
    await db_session.commit()

    # Unlock + edit in one payload — rejected because the row is
    # still locked when the write evaluates.
    with pytest.raises(HTTPException) as exc_info:
        await update_position(
            position_id=position.id,
            data=PositionUpdate(is_locked=False, menge=42.0),
            user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# 3. Cross-tenant protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_update_user_as_position(
    db_session: AsyncSession,
):
    """User B authenticated, but the position belongs to User A's
    project. ``verify_lv_owner`` must reject — 403."""
    user_a, _, position = await _seed_position(
        db_session, email_prefix="usera"
    )

    user_b = User(
        id=uuid.uuid4(),
        email=f"userb-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="User B",
    )
    db_session.add(user_b)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await update_position(
            position_id=position.id,
            data=PositionUpdate(menge=99.0),
            user=user_b,
            db=db_session,
        )
    # ``verify_lv_owner`` raises 403 on ownership mismatch (existing
    # behaviour pre-v23.5). The exact code matters less than the
    # rejection — any 4xx other than the 409 lock-gate signals the
    # ownership check fired.
    assert exc_info.value.status_code in {403, 404}

    await db_session.rollback()
    await db_session.refresh(position)
    # Untouched by the failed attempt.
    assert float(position.menge) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 4. Partial update — only specified fields write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_position_partial_only_writes_named_fields(
    db_session: AsyncSession,
):
    """Sending only ``einheitspreis`` must NOT clobber ``menge``.
    Pydantic's ``exclude_unset`` semantics are what makes this safe;
    pin the behaviour so a future schema refactor can't quietly
    re-introduce the "always overwrite" footgun."""
    user, _, position = await _seed_position(
        db_session,
        menge=Decimal("12.500"),
        einheitspreis=Decimal("3.00"),
    )

    await update_position(
        position_id=position.id,
        data=PositionUpdate(einheitspreis=4.00),
        user=user,
        db=db_session,
    )
    await db_session.commit()
    await db_session.refresh(position)
    assert float(position.menge) == pytest.approx(12.5)  # untouched
    assert float(position.einheitspreis) == pytest.approx(4.0)
