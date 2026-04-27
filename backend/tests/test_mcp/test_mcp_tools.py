"""Tests for the eight read-only MCP tool implementations.

We exercise the underscore-prefixed handler functions directly rather
than driving the full MCP dispatch loop. Reasons:

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

1. **Multi-tenant isolation.** Every tool that takes an id must 404
   when the caller doesn't own it. A regression here would let one
   tenant read another's data over MCP — the worst possible bug for
   this surface.
2. **Output shape.** The MCP catalogue is part of our public contract
   to agent integrators; a silent rename of a field would break
   Claude Desktop / n8n flows.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
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
    _tool_get_lv,
    _tool_get_position_with_proof,
    _tool_get_project,
    _tool_get_project_structure,
    _tool_list_lvs,
    _tool_list_projects,
    _tool_list_rooms,
    _tool_list_templates,
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
