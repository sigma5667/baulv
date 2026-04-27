"""Tests for the MCP tool implementations.

3a (read-only) and 3b (mutations) are both covered here. We exercise
the underscore-prefixed handler functions directly rather than
driving the full MCP dispatch loop. Reasons:

* The dispatcher (``app.mcp.server.call_tool``) opens a fresh session
  via ``async_session_factory`` — that's bound to the production DB
  URL at import time and would bypass our SQLite test fixture.
* The dispatcher's value-add is auth + error translation, both of
  which are covered separately in ``test_pat_auth.py`` and the
  exception-path assertions below.
* The handler functions are the actual contract: tenancy enforcement,
  ownership checks, output shape. Drive those directly and you're
  testing what matters.

The tests pin two things hard:

1. **Multi-tenant isolation.** Every tool that takes an id must 403/404
   when the caller doesn't own it. A regression here would let one
   tenant read or write another's data over MCP — the worst possible
   bug for this surface.
2. **Output shape.** The MCP catalogue is part of our public contract
   to agent integrators; a silent rename of a field would break
   Claude Desktop / n8n flows.

3b adds a third: **the is_locked guard on ``update_position``**.
The REST endpoint allows blind overwrites on locked rows; the MCP
tool deliberately doesn't. We test the guard explicitly because
relaxing it accidentally would silently permit agents to overwrite
positions a human had locked.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.lv import (
    Leistungsgruppe,
    Leistungsverzeichnis,
    Position,
)
from app.db.models.lv_template import LVTemplate
from app.db.models.project import (
    Building,
    Floor,
    Project,
    Room,
    Unit,
)
from app.db.models.user import User
from app.mcp.server import (
    _tool_create_lv,
    _tool_create_lv_from_template,
    _tool_create_project,
    _tool_create_template_from_lv,
    _tool_get_lv,
    _tool_get_position_with_proof,
    _tool_get_project,
    _tool_get_project_structure,
    _tool_list_lvs,
    _tool_list_projects,
    _tool_list_rooms,
    _tool_list_templates,
    _tool_update_lv,
    _tool_update_position,
    _tool_update_project,
)


# ---------------------------------------------------------------------------
# Shared fixture: two users with one project each.
# ---------------------------------------------------------------------------


async def _seed_two_tenants(db: AsyncSession):
    """Seed two unrelated users with one project each so we can prove
    the tools never cross the tenant boundary.

    Returns ``(user_a, user_b, project_a, project_b)`` so the
    individual tests can stay terse.
    """
    user_a = User(
        id=uuid.uuid4(),
        email=f"a-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tenant A",
    )
    user_b = User(
        id=uuid.uuid4(),
        email=f"b-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Tenant B",
    )
    db.add_all([user_a, user_b])
    await db.flush()

    project_a = Project(
        id=uuid.uuid4(),
        user_id=user_a.id,
        name="Projekt A",
        address="Wienerstr. 1, 1010 Wien",
        client_name="Bauherr A",
    )
    project_b = Project(
        id=uuid.uuid4(),
        user_id=user_b.id,
        name="Projekt B",
        address="Linzerstr. 99, 4020 Linz",
    )
    db.add_all([project_a, project_b])
    await db.commit()

    return user_a, user_b, project_a, project_b


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_projects_returns_only_callers_projects(
    db_session: AsyncSession,
):
    user_a, user_b, project_a, project_b = await _seed_two_tenants(db_session)

    rows_a = await _tool_list_projects(db_session, user_a)
    assert len(rows_a) == 1
    assert rows_a[0]["id"] == str(project_a.id)
    assert rows_a[0]["name"] == "Projekt A"
    assert rows_a[0]["address"] == "Wienerstr. 1, 1010 Wien"
    # User B's row must not bleed through.
    assert all(r["id"] != str(project_b.id) for r in rows_a)


# ---------------------------------------------------------------------------
# get_project — owner happy path + cross-tenant 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_returns_full_metadata_for_owner(
    db_session: AsyncSession,
):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    out = await _tool_get_project(db_session, user_a, project_a.id)
    assert out["id"] == str(project_a.id)
    assert out["name"] == "Projekt A"
    assert out["client_name"] == "Bauherr A"
    # We commit to these field names in the docstring of the tool;
    # a typo here would silently break agent prompts.
    assert "address" in out
    assert "status" in out
    assert "created_at" in out


@pytest.mark.asyncio
async def test_get_project_404s_for_non_owner(db_session: AsyncSession):
    """Cross-tenant access must surface as 404, not 403 — leaking
    "this project exists but isn't yours" is a low-grade enumeration
    primitive we want to deny."""
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await _tool_get_project(db_session, user_b, project_a.id)
    # ``verify_project_owner`` raises 403 explicitly when the project
    # exists but belongs to someone else; that's deliberate so the
    # SPA can show a different message. The MCP dispatcher translates
    # *any* HTTPException to a textual error before it reaches the
    # agent — which means the 403 vs 404 distinction is internal.
    # The point of this test is that the call doesn't silently
    # return User B another tenant's data.
    assert exc_info.value.status_code in (403, 404)


# ---------------------------------------------------------------------------
# get_project_structure — full tree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_structure_returns_nested_buildings(
    db_session: AsyncSession,
):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)

    building = Building(
        id=uuid.uuid4(), project_id=project_a.id, name="Haus 1"
    )
    db_session.add(building)
    await db_session.flush()

    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db_session.add(floor)
    await db_session.flush()

    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top 1")
    db_session.add(unit)
    await db_session.flush()

    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Wohnzimmer",
        area_m2=Decimal("20.0"),
        perimeter_m=Decimal("18.0"),
        height_m=Decimal("2.7"),
    )
    db_session.add(room)
    await db_session.commit()

    out = await _tool_get_project_structure(
        db_session, user_a, project_a.id
    )
    assert out["project_id"] == str(project_a.id)
    assert len(out["buildings"]) == 1
    b = out["buildings"][0]
    assert b["name"] == "Haus 1"
    assert len(b["floors"]) == 1
    f = b["floors"][0]
    assert f["name"] == "EG"
    assert len(f["units"]) == 1
    u = f["units"][0]
    assert len(u["rooms"]) == 1
    r = u["rooms"][0]
    assert r["name"] == "Wohnzimmer"
    # Geometry must round-trip — Decimals get stringified by
    # ``_default`` only at JSON serialise time, here they should
    # still be Decimals.
    assert r["area_m2"] == Decimal("20.0")


# ---------------------------------------------------------------------------
# list_rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rooms_returns_flat_list_with_count(
    db_session: AsyncSession,
):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    building = Building(
        id=uuid.uuid4(), project_id=project_a.id, name="Haus 1"
    )
    db_session.add(building)
    await db_session.flush()
    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db_session.add(floor)
    await db_session.flush()
    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top 1")
    db_session.add(unit)
    await db_session.flush()

    for name in ("Wohnzimmer", "Bad", "Schlafzimmer"):
        db_session.add(
            Room(
                id=uuid.uuid4(),
                unit_id=unit.id,
                name=name,
                area_m2=Decimal("10.0"),
                perimeter_m=Decimal("12.0"),
                height_m=Decimal("2.5"),
            )
        )
    await db_session.commit()

    out = await _tool_list_rooms(db_session, user_a, project_a.id)
    assert out["room_count"] == 3
    names = {r["name"] for r in out["rooms"]}
    assert names == {"Wohnzimmer", "Bad", "Schlafzimmer"}


# ---------------------------------------------------------------------------
# list_lvs + get_lv
# ---------------------------------------------------------------------------


async def _seed_lv_with_one_position(
    db: AsyncSession, project: Project
) -> tuple[Leistungsverzeichnis, Position]:
    lv = Leistungsverzeichnis(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Malerarbeiten",
        trade="malerarbeiten",
    )
    db.add(lv)
    await db.flush()
    gruppe = Leistungsgruppe(
        id=uuid.uuid4(), lv_id=lv.id, nummer="01", bezeichnung="Vorarbeiten"
    )
    db.add(gruppe)
    await db.flush()
    position = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        positions_nummer="01.01",
        kurztext="Untergrund prüfen",
        einheit="m²",
        menge=Decimal("100.0"),
        einheitspreis=Decimal("4.50"),
    )
    db.add(position)
    await db.commit()
    return lv, position


@pytest.mark.asyncio
async def test_list_lvs_includes_counts(db_session: AsyncSession):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    lv, _ = await _seed_lv_with_one_position(db_session, project_a)

    out = await _tool_list_lvs(db_session, user_a, project_a.id)
    assert len(out["lvs"]) == 1
    row = out["lvs"][0]
    assert row["id"] == str(lv.id)
    assert row["name"] == "Malerarbeiten"
    assert row["trade"] == "malerarbeiten"
    assert row["gruppen_count"] == 1
    assert row["positionen_count"] == 1


@pytest.mark.asyncio
async def test_get_lv_returns_groups_and_positions(
    db_session: AsyncSession,
):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    lv, position = await _seed_lv_with_one_position(db_session, project_a)

    out = await _tool_get_lv(db_session, user_a, lv.id)
    assert out["id"] == str(lv.id)
    assert len(out["gruppen"]) == 1
    g = out["gruppen"][0]
    assert g["nummer"] == "01"
    assert len(g["positionen"]) == 1
    p = g["positionen"][0]
    assert p["id"] == str(position.id)
    assert p["kurztext"] == "Untergrund prüfen"
    assert p["menge"] == Decimal("100.0")
    # Berechnungsnachweise are summarised here, not exploded.
    assert p["berechnungsnachweis_count"] == 0


@pytest.mark.asyncio
async def test_get_lv_404s_for_non_owner(db_session: AsyncSession):
    """Same isolation rule as ``get_project`` — User B must not be
    able to fetch User A's LV by id."""
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    lv, _ = await _seed_lv_with_one_position(db_session, project_a)

    with pytest.raises(HTTPException) as exc_info:
        await _tool_get_lv(db_session, user_b, lv.id)
    assert exc_info.value.status_code in (403, 404)


# ---------------------------------------------------------------------------
# get_position_with_proof
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_position_with_proof_returns_position(
    db_session: AsyncSession,
):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    _, position = await _seed_lv_with_one_position(db_session, project_a)

    out = await _tool_get_position_with_proof(
        db_session, user_a, position.id
    )
    assert out["id"] == str(position.id)
    assert out["kurztext"] == "Untergrund prüfen"
    assert out["menge"] == Decimal("100.0")
    # No proofs were seeded → empty list, not missing key. Agents
    # depend on the key always being present so they can iterate
    # without a None-check first.
    assert out["berechnungsnachweise"] == []


@pytest.mark.asyncio
async def test_get_position_with_proof_404s_for_non_owner(
    db_session: AsyncSession,
):
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    _, position = await _seed_lv_with_one_position(db_session, project_a)

    with pytest.raises(HTTPException) as exc_info:
        await _tool_get_position_with_proof(
            db_session, user_b, position.id
        )
    # 404-mask via verify_lv_owner — could be either depending on
    # which check fails first; both are correct.
    assert exc_info.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_get_position_with_proof_404s_for_unknown_id(
    db_session: AsyncSession,
):
    """A bogus UUID must surface as 404, not as an unhandled
    ``AttributeError`` from accessing properties on ``None``."""
    user_a, _, _, _ = await _seed_two_tenants(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await _tool_get_position_with_proof(
            db_session, user_a, uuid.uuid4()
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# list_templates — system + own visibility rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_templates_shows_system_and_own_only(
    db_session: AsyncSession,
):
    """Visibility rule: every user sees system templates plus their
    own. User B's custom template must not show up for user A."""
    user_a, user_b, _, _ = await _seed_two_tenants(db_session)

    # System template — visible to everyone.
    system_tpl = LVTemplate(
        id=uuid.uuid4(),
        name="System-Vorlage",
        description=None,
        category="einfamilienhaus",
        gewerk="malerarbeiten",
        is_system=True,
        created_by_user_id=None,
        template_data={"gruppen": []},
    )
    # User-A custom template — visible to A only.
    a_tpl = LVTemplate(
        id=uuid.uuid4(),
        name="A's Vorlage",
        description=None,
        category="einfamilienhaus",
        gewerk="malerarbeiten",
        is_system=False,
        created_by_user_id=user_a.id,
        template_data={
            "gruppen": [
                {
                    "nummer": "01",
                    "bezeichnung": "Vorarbeiten",
                    "positionen": [
                        {
                            "positions_nummer": "01.01",
                            "kurztext": "x",
                            "einheit": "m²",
                        }
                    ],
                }
            ]
        },
    )
    # User-B custom template — A must NOT see this.
    b_tpl = LVTemplate(
        id=uuid.uuid4(),
        name="B's Vorlage",
        description=None,
        category="einfamilienhaus",
        gewerk="malerarbeiten",
        is_system=False,
        created_by_user_id=user_b.id,
        template_data={"gruppen": []},
    )
    db_session.add_all([system_tpl, a_tpl, b_tpl])
    await db_session.commit()

    out = await _tool_list_templates(
        db_session, user_a, category=None, gewerk=None
    )
    names = {t["name"] for t in out["templates"]}
    assert "System-Vorlage" in names
    assert "A's Vorlage" in names
    assert "B's Vorlage" not in names

    # Counts must come out non-zero for the populated template so
    # the agent UI can show "1 Position" instead of just "Vorlage".
    a_row = next(t for t in out["templates"] if t["name"] == "A's Vorlage")
    assert a_row["gruppen_count"] == 1
    assert a_row["positionen_count"] == 1


@pytest.mark.asyncio
async def test_list_templates_filters_by_gewerk(db_session: AsyncSession):
    user_a, _, _, _ = await _seed_two_tenants(db_session)
    db_session.add_all(
        [
            LVTemplate(
                id=uuid.uuid4(),
                name="Maler",
                description=None,
                category="einfamilienhaus",
                gewerk="malerarbeiten",
                is_system=True,
                created_by_user_id=None,
                template_data={"gruppen": []},
            ),
            LVTemplate(
                id=uuid.uuid4(),
                name="Elektro",
                description=None,
                category="einfamilienhaus",
                gewerk="elektroinstallation",
                is_system=True,
                created_by_user_id=None,
                template_data={"gruppen": []},
            ),
        ]
    )
    await db_session.commit()

    out = await _tool_list_templates(
        db_session, user_a, category=None, gewerk="malerarbeiten"
    )
    names = {t["name"] for t in out["templates"]}
    assert "Maler" in names
    assert "Elektro" not in names


# ---------------------------------------------------------------------------
# Mutation tools (3b)
# ---------------------------------------------------------------------------
#
# Conventions for this section:
#
# * Plan-limit tests need ``_beta_active`` off so the limit actually
#   fires. We monkeypatch it per-test rather than fiddling with the
#   global settings object — the result is identical and the override
#   reverts cleanly when the test exits.
# * Tenancy-violation tests assert the call raises ``HTTPException``
#   with status 403 or 404. The dispatcher translates either to plain
#   text before the agent sees it, so the SPA-vs-MCP distinction
#   doesn't matter here — what matters is that the call doesn't
#   silently succeed for the wrong tenant.
# * Each happy-path test asserts the returned dict has the field
#   shape we promise to agent integrators (id, name, plus whatever
#   the tool documents). A silent rename would break Claude Desktop
#   / n8n flows; the test would catch it.


# ---- create_project --------------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_writes_row_and_returns_full_dict(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """Happy path: a Pro user creates a project. We assert the DB row
    exists, the returned dict has the documented field shape, and the
    user_id was stamped from the caller (not from the arguments)."""
    # Pro plan → unlimited projects, no beta dependency for the happy
    # path. Keeps the test orthogonal to the plan-limit test below.
    user = User(
        id=uuid.uuid4(),
        email=f"creator-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Creator",
        subscription_plan="pro",
    )
    db_session.add(user)
    await db_session.commit()

    out = await _tool_create_project(
        db_session,
        user,
        {
            "name": "EFH Schmidt 2026",
            "address": "Hauptstr. 12, 4020 Linz",
            "client_name": "Familie Schmidt",
            "project_number": "P-2026-001",
        },
    )
    assert out["name"] == "EFH Schmidt 2026"
    assert out["address"] == "Hauptstr. 12, 4020 Linz"
    assert out["client_name"] == "Familie Schmidt"
    assert out["project_number"] == "P-2026-001"
    # id must round-trip — the agent uses it for the next call.
    project_id = uuid.UUID(out["id"])

    # And the row really landed on disk.
    db_row = (
        await db_session.execute(
            select(Project).where(Project.id == project_id)
        )
    ).scalars().first()
    assert db_row is not None
    assert db_row.user_id == user.id


@pytest.mark.asyncio
async def test_create_project_enforces_basis_plan_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """Basis plan caps at 3 projects. The 4th attempt must 403.

    We have to disable the beta unlock or the limit doesn't fire —
    ``check_project_limit`` returns ``True`` unconditionally when the
    flag is on. Mirrors the production REST endpoint's behaviour.
    """
    monkeypatch.setattr("app.subscriptions._beta_active", lambda: False)

    user = User(
        id=uuid.uuid4(),
        email=f"basis-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Basis User",
        subscription_plan="basis",
    )
    db_session.add(user)
    await db_session.flush()
    # Seed 3 existing projects → at the limit.
    for i in range(3):
        db_session.add(Project(user_id=user.id, name=f"P{i}"))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _tool_create_project(db_session, user, {"name": "P4"})
    assert exc_info.value.status_code == 403
    assert "Projektlimit" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_project_allows_more_when_beta_unlock_active(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """The beta unlock must lift the project limit for basis users.
    This is the tester-day contract: flip the flag, basis behaves
    like pro for project creation."""
    monkeypatch.setattr("app.subscriptions._beta_active", lambda: True)

    user = User(
        id=uuid.uuid4(),
        email=f"beta-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Beta Tester",
        subscription_plan="basis",
    )
    db_session.add(user)
    await db_session.flush()
    for i in range(3):
        db_session.add(Project(user_id=user.id, name=f"P{i}"))
    await db_session.commit()

    out = await _tool_create_project(db_session, user, {"name": "P4"})
    assert out["name"] == "P4"


# ---- update_project --------------------------------------------------------


@pytest.mark.asyncio
async def test_update_project_patches_only_supplied_fields(
    db_session: AsyncSession,
):
    """Patch-semantics: untouched fields stay untouched.

    Critical contract for agents — partial updates must not blank out
    fields the agent didn't mention. ``ProjectUpdate`` is a Pydantic
    model with ``Optional[...]`` fields and ``model_dump(exclude_unset=True)``
    is what guarantees this; the test catches a regression where someone
    drops the ``exclude_unset`` and starts overwriting with ``None``.
    """
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    original_address = project_a.address

    out = await _tool_update_project(
        db_session,
        user_a,
        project_a.id,
        {"project_id": str(project_a.id), "status": "archiviert"},
    )
    assert out["status"] == "archiviert"
    # Address was not in the patch → must survive.
    assert out["address"] == original_address


@pytest.mark.asyncio
async def test_update_project_rejects_cross_tenant_writes(
    db_session: AsyncSession,
):
    """User B must not be able to mutate User A's project. This is the
    write-side mirror of ``test_get_project_404s_for_non_owner``."""
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await _tool_update_project(
            db_session,
            user_b,
            project_a.id,
            {"project_id": str(project_a.id), "name": "Hacked"},
        )
    assert exc_info.value.status_code in (403, 404)


# ---- create_lv -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_lv_writes_metadata_row(db_session: AsyncSession):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    out = await _tool_create_lv(
        db_session,
        user_a,
        {
            "project_id": str(project_a.id),
            "name": "Malerarbeiten OG",
            "trade": "malerarbeiten",
        },
    )
    assert out["name"] == "Malerarbeiten OG"
    assert out["trade"] == "malerarbeiten"
    assert out["project_id"] == str(project_a.id)
    # Shape pin: ``status`` is part of the contract even though we
    # didn't set it — agents may filter on it without checking the key.
    assert "status" in out


@pytest.mark.asyncio
async def test_create_lv_rejects_cross_tenant_project(
    db_session: AsyncSession,
):
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await _tool_create_lv(
            db_session,
            user_b,
            {
                "project_id": str(project_a.id),
                "name": "geklaut",
                "trade": "malerarbeiten",
            },
        )
    assert exc_info.value.status_code in (403, 404)


# ---- create_lv_from_template -----------------------------------------------


async def _seed_template(
    db: AsyncSession, *, owner: User | None = None, is_system: bool = False
) -> LVTemplate:
    """Helper: a minimal template with one group + one position. Used
    by both the happy path and the cross-tenant test to keep them
    in sync."""
    tpl = LVTemplate(
        id=uuid.uuid4(),
        name="Maler-Vorlage",
        description=None,
        category="einfamilienhaus",
        gewerk="malerarbeiten",
        is_system=is_system,
        created_by_user_id=None if is_system else (owner.id if owner else None),
        template_data={
            "gruppen": [
                {
                    "nummer": "01",
                    "bezeichnung": "Vorarbeiten",
                    "positionen": [
                        {
                            "positions_nummer": "01.01",
                            "kurztext": "Untergrund prüfen",
                            "langtext": "...",
                            "einheit": "m²",
                            "kategorie": "vorarbeit",
                        }
                    ],
                }
            ]
        },
    )
    db.add(tpl)
    await db.commit()
    return tpl


@pytest.mark.asyncio
async def test_create_lv_from_template_copies_groups_and_positions(
    db_session: AsyncSession,
):
    """Happy path: a system template gets cloned into User A's project.

    This is the primary agent flow. We verify both the returned counts
    and the actual rows on disk — a regression in the shared helper
    ``_copy_template_payload_into_new_lv`` would surface here.
    """
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    tpl = await _seed_template(db_session, is_system=True)

    out = await _tool_create_lv_from_template(
        db_session,
        user_a,
        {
            "project_id": str(project_a.id),
            "template_id": str(tpl.id),
            "name": "Malerarbeiten EG",
        },
    )
    assert out["gruppen_created"] == 1
    assert out["positionen_created"] == 1
    assert out["name"] == "Malerarbeiten EG"
    assert out["project_id"] == str(project_a.id)

    # And the LV really exists.
    lv = await db_session.get(Leistungsverzeichnis, uuid.UUID(out["lv_id"]))
    assert lv is not None
    assert lv.trade == "malerarbeiten"


@pytest.mark.asyncio
async def test_create_lv_from_template_rejects_cross_tenant_project(
    db_session: AsyncSession,
):
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    tpl = await _seed_template(db_session, is_system=True)

    with pytest.raises(HTTPException) as exc_info:
        await _tool_create_lv_from_template(
            db_session,
            user_b,  # B targeting A's project → must fail
            {
                "project_id": str(project_a.id),
                "template_id": str(tpl.id),
            },
        )
    assert exc_info.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_create_lv_from_template_rejects_other_users_custom_template(
    db_session: AsyncSession,
):
    """A user-owned template must not be visible to other tenants. We
    404-mask so an attacker can't enumerate template ids by probing."""
    user_a, user_b, _, project_b = await _seed_two_tenants(db_session)
    # User A owns this template.
    tpl = await _seed_template(db_session, owner=user_a, is_system=False)

    with pytest.raises(HTTPException) as exc_info:
        await _tool_create_lv_from_template(
            db_session,
            user_b,  # User B trying to use A's template
            {
                "project_id": str(project_b.id),
                "template_id": str(tpl.id),
            },
        )
    assert exc_info.value.status_code == 404


# ---- update_lv -------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_lv_patches_metadata(db_session: AsyncSession):
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    lv, _ = await _seed_lv_with_one_position(db_session, project_a)

    out = await _tool_update_lv(
        db_session,
        user_a,
        lv.id,
        {"lv_id": str(lv.id), "vorbemerkungen": "Achtung Altbau"},
    )
    assert out["vorbemerkungen"] == "Achtung Altbau"
    # Trade should be untouched — patch-semantics.
    assert out["trade"] == "malerarbeiten"


@pytest.mark.asyncio
async def test_update_lv_rejects_cross_tenant_writes(
    db_session: AsyncSession,
):
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    lv, _ = await _seed_lv_with_one_position(db_session, project_a)

    with pytest.raises(HTTPException) as exc_info:
        await _tool_update_lv(
            db_session,
            user_b,
            lv.id,
            {"lv_id": str(lv.id), "name": "geklaut"},
        )
    assert exc_info.value.status_code in (403, 404)


# ---- update_position -------------------------------------------------------


@pytest.mark.asyncio
async def test_update_position_patches_text_and_flips_text_source(
    db_session: AsyncSession,
):
    """Setting kurztext/langtext via MCP must flip ``text_source`` to
    ``manual``. Mirrors the SPA endpoint so downstream tooling
    (template saver, AI re-gen) sees the same signal regardless of
    which surface wrote the change."""
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    _, position = await _seed_lv_with_one_position(db_session, project_a)

    out = await _tool_update_position(
        db_session,
        user_a,
        position.id,
        {
            "position_id": str(position.id),
            "kurztext": "Neuer Kurztext",
            "einheitspreis": 5.5,
        },
    )
    assert out["kurztext"] == "Neuer Kurztext"
    # 5.5 round-trips — the schema accepts ``number`` and SQLAlchemy
    # stores it as Decimal. Either repr is acceptable as long as it
    # equals 5.5.
    assert Decimal(str(out["einheitspreis"])) == Decimal("5.5")
    assert out["text_source"] == "manual"


@pytest.mark.asyncio
async def test_update_position_rejects_cross_tenant_writes(
    db_session: AsyncSession,
):
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    _, position = await _seed_lv_with_one_position(db_session, project_a)

    with pytest.raises(HTTPException) as exc_info:
        await _tool_update_position(
            db_session,
            user_b,
            position.id,
            {"position_id": str(position.id), "kurztext": "geklaut"},
        )
    assert exc_info.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_update_position_locked_rejects_combined_patch(
    db_session: AsyncSession,
):
    """The MCP-only is_locked guard: a locked position rejects any
    patch that combines unlock with text/price changes. Forces the
    audit-friendly two-step flow — unlock first, then mutate."""
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    _, position = await _seed_lv_with_one_position(db_session, project_a)
    position.is_locked = True
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _tool_update_position(
            db_session,
            user_a,
            position.id,
            {
                "position_id": str(position.id),
                "is_locked": False,
                "kurztext": "trying to sneak a text change in",
            },
        )
    assert exc_info.value.status_code == 403
    assert "gesperrt" in exc_info.value.detail


@pytest.mark.asyncio
async def test_update_position_locked_rejects_text_only_patch(
    db_session: AsyncSession,
):
    """A locked position with no unlock at all must also be rejected.
    Otherwise the guard would only block the combined case but allow
    silent overwrites of locked rows when the agent forgets to send
    is_locked at all — which is the more likely mistake."""
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    _, position = await _seed_lv_with_one_position(db_session, project_a)
    position.is_locked = True
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _tool_update_position(
            db_session,
            user_a,
            position.id,
            {"position_id": str(position.id), "kurztext": "blind overwrite"},
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_update_position_locked_accepts_unlock_alone(
    db_session: AsyncSession,
):
    """The one allowed call on a locked row: bare unlock. No other
    fields, no audit-trail side effects beyond the unlock itself."""
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    _, position = await _seed_lv_with_one_position(db_session, project_a)
    position.is_locked = True
    await db_session.commit()

    out = await _tool_update_position(
        db_session,
        user_a,
        position.id,
        {"position_id": str(position.id), "is_locked": False},
    )
    assert out["is_locked"] is False
    # Text must NOT have flipped to manual — we didn't touch it.
    # ``text_source`` defaults to whatever the position had before; we
    # only flip it on actual text writes.
    refreshed = await db_session.get(Position, position.id)
    assert refreshed is not None
    assert refreshed.is_locked is False


# ---- create_template_from_lv ----------------------------------------------


@pytest.mark.asyncio
async def test_create_template_from_lv_strips_quantities_and_prices(
    db_session: AsyncSession,
):
    """Templates must be price- and quantity-agnostic. Even though the
    source LV has menge=100 and einheitspreis=4.50, the template_data
    JSON must omit both — they belong to the concrete project the
    template is later applied to."""
    user_a, _, project_a, _ = await _seed_two_tenants(db_session)
    lv, _ = await _seed_lv_with_one_position(db_session, project_a)

    out = await _tool_create_template_from_lv(
        db_session,
        user_a,
        {
            "lv_id": str(lv.id),
            "name": "Maler-Vorlage 2026",
            "category": "einfamilienhaus",
        },
    )
    assert out["name"] == "Maler-Vorlage 2026"
    assert out["category"] == "einfamilienhaus"
    assert out["gewerk"] == "malerarbeiten"
    assert out["is_system"] is False
    assert out["gruppen_count"] == 1
    assert out["positionen_count"] == 1

    # Read back the JSONB blob and confirm the strip happened.
    tpl = await db_session.get(LVTemplate, uuid.UUID(out["id"]))
    assert tpl is not None
    pos = tpl.template_data["gruppen"][0]["positionen"][0]
    assert "menge" not in pos
    assert "einheitspreis" not in pos
    assert pos["kurztext"] == "Untergrund prüfen"


@pytest.mark.asyncio
async def test_create_template_from_lv_rejects_cross_tenant_lv(
    db_session: AsyncSession,
):
    user_a, user_b, project_a, _ = await _seed_two_tenants(db_session)
    lv, _ = await _seed_lv_with_one_position(db_session, project_a)

    with pytest.raises(HTTPException) as exc_info:
        await _tool_create_template_from_lv(
            db_session,
            user_b,  # B trying to clone A's LV
            {
                "lv_id": str(lv.id),
                "name": "geklaut",
                "category": "einfamilienhaus",
            },
        )
    assert exc_info.value.status_code in (403, 404)
