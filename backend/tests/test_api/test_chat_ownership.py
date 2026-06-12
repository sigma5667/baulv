"""Security-Regression — Chat-Session Mandantentrennung (Audit 2026-06).

Lockt den IDOR-Fix, damit er nicht still zurückkippt. Vorher:

* ``GET /chat/sessions`` ohne ``project_id`` war ``select(ChatSession)``
  ungefiltert → jeder eingeloggte User sah die Sessions ALLER Mandanten.
* ``verify_chat_session_owner`` ließ projektlose ("globale") Sessions
  für jeden Authentifizierten durch → fremde Sessions les-/umbenenn-/
  lösch-/beschreibbar.

Nach dem Fix gehört jede Session genau einem User (``ChatSession.user_id``,
NOT NULL); beide Pfade filtern bzw. prüfen darauf.

Wir rufen die Endpoint-Funktionen direkt auf (kein HTTP-Layer), in der
``db_session``-Fixture aus conftest.py.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.chat import list_sessions
from app.api.ownership import verify_chat_session_owner
from app.db.models.chat import ChatSession
from app.db.models.project import Project
from app.db.models.user import User


async def _seed_user(db: AsyncSession, *, prefix: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{prefix}-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name=f"{prefix} Tester",
        company_name=f"{prefix} GmbH",
    )
    db.add(user)
    await db.flush()
    return user


# ---------------------------------------------------------------------------
# GET /chat/sessions — darf nur eigene Sessions zurückgeben
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_excludes_other_users_sessions(
    db_session: AsyncSession,
):
    """User B darf User A's Sessions nie in der Liste sehen — auch
    nicht die projektlosen, die vor dem Fix herrenlos waren."""
    user_a = await _seed_user(db_session, prefix="alice")
    user_b = await _seed_user(db_session, prefix="bob")

    # Zwei Sessions für A: eine projektlos, eine projektgebunden.
    project_a = Project(id=uuid.uuid4(), user_id=user_a.id, name="A-Projekt")
    db_session.add(project_a)
    await db_session.flush()

    sess_a_global = ChatSession(
        id=uuid.uuid4(), user_id=user_a.id, project_id=None, title="A global"
    )
    sess_a_project = ChatSession(
        id=uuid.uuid4(), user_id=user_a.id, project_id=project_a.id, title="A projekt"
    )
    # Eine eigene Session für B, damit wir auch das Positiv-Ergebnis sehen.
    sess_b = ChatSession(
        id=uuid.uuid4(), user_id=user_b.id, project_id=None, title="B global"
    )
    db_session.add_all([sess_a_global, sess_a_project, sess_b])
    await db_session.commit()

    # B listet ohne project_id-Filter → bekommt NUR die eigene Session.
    b_sessions = await list_sessions(project_id=None, user=user_b, db=db_session)
    b_ids = {s.id for s in b_sessions}
    assert sess_b.id in b_ids
    assert sess_a_global.id not in b_ids, (
        "IDOR-Regression: B sieht A's projektlose Session!"
    )
    assert sess_a_project.id not in b_ids, (
        "IDOR-Regression: B sieht A's projektgebundene Session!"
    )

    # A listet → bekommt beide eigenen, nicht B's.
    a_sessions = await list_sessions(project_id=None, user=user_a, db=db_session)
    a_ids = {s.id for s in a_sessions}
    assert a_ids == {sess_a_global.id, sess_a_project.id}


# ---------------------------------------------------------------------------
# verify_chat_session_owner — 404 bei fremdem User
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_chat_session_owner_rejects_foreign_global_session(
    db_session: AsyncSession,
):
    """Projektlose Session von A → B bekommt 404 (nicht 403, keine
    Existenz-Bestätigung). Das war die Kern-IDOR-Lücke."""
    user_a = await _seed_user(db_session, prefix="alice")
    user_b = await _seed_user(db_session, prefix="bob")

    sess_a = ChatSession(
        id=uuid.uuid4(), user_id=user_a.id, project_id=None, title="A global"
    )
    db_session.add(sess_a)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await verify_chat_session_owner(sess_a.id, user_b, db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_verify_chat_session_owner_rejects_foreign_project_session(
    db_session: AsyncSession,
):
    """Auch projektgebundene Session von A → B bekommt 404. Deckt den
    früheren transitiven Pfad (über project_id) ab."""
    user_a = await _seed_user(db_session, prefix="alice")
    user_b = await _seed_user(db_session, prefix="bob")

    project_a = Project(id=uuid.uuid4(), user_id=user_a.id, name="A-Projekt")
    db_session.add(project_a)
    await db_session.flush()
    sess_a = ChatSession(
        id=uuid.uuid4(), user_id=user_a.id, project_id=project_a.id, title="A projekt"
    )
    db_session.add(sess_a)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await verify_chat_session_owner(sess_a.id, user_b, db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_verify_chat_session_owner_allows_own_session(
    db_session: AsyncSession,
):
    """Positiv-Kontrolle: der echte Eigentümer kommt durch und kriegt
    die Session zurück."""
    user_a = await _seed_user(db_session, prefix="alice")
    sess_a = ChatSession(
        id=uuid.uuid4(), user_id=user_a.id, project_id=None, title="A global"
    )
    db_session.add(sess_a)
    await db_session.commit()

    result = await verify_chat_session_owner(sess_a.id, user_a, db_session)
    assert result.id == sess_a.id
    assert result.user_id == user_a.id


@pytest.mark.asyncio
async def test_verify_chat_session_owner_404_for_missing_session(
    db_session: AsyncSession,
):
    """Nicht-existente Session-ID → 404, selbe Antwort wie bei fremder
    Session (keine Unterscheidung leakt)."""
    user_a = await _seed_user(db_session, prefix="alice")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await verify_chat_session_owner(uuid.uuid4(), user_a, db_session)
    assert exc_info.value.status_code == 404
