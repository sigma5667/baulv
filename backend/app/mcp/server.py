"""The MCP ``Server`` instance and the eight read-only tools we expose.

3a is intentionally read-only. We want agents to be able to *describe*
a user's BauLV state — list projects, walk the building tree, dump an
LV with its positions and proofs — without yet being able to mutate
anything. Mutation lands in 3b once we've shaken out auth and seen
real-world usage patterns.

Lifecycle
=========

The MCP SDK is built around a process-singleton ``Server`` registered
with handlers at import time. We follow that pattern; the per-request
state we care about (which user is calling) lives in the
``current_user_id_var`` contextvar set by the SSE transport just
before ``server.run`` enters its dispatch loop.

Each tool call:

1. Reads the user_id from the contextvar (raises ``LookupError`` if
   the auth layer didn't run — that's a programmer error, not a
   normal failure mode, so we let it propagate).
2. Opens a fresh ``AsyncSession`` — connections are short-lived and
   scoped to one tool call, never the whole SSE conversation.
3. Reuses the existing ``app.api.ownership`` helpers so the tenancy
   model is the same one the SPA endpoints enforce. If ownership
   fails, the helper raises ``HTTPException``; we translate that
   into a textual error so the MCP client sees a readable message
   instead of a stack trace.
4. Returns ``[TextContent(text=...)]`` containing JSON. JSON is the
   lingua franca for tool outputs in MCP — clients (Claude Desktop,
   ChatGPT, n8n) all parse it the same way, and it round-trips
   cleanly through the protocol's text channel.

Output schema stability
=======================

Tool output schemas are part of our agent-facing contract. We avoid
returning bare SQLAlchemy ORM objects (their attribute set is an
implementation detail); each handler explicitly projects to a dict
with the fields we want to commit to. ``Decimal`` and ``UUID`` are
stringified via ``_default`` so the output is JSON-RFC compliant.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import mcp.types as mcp_types
from fastapi import HTTPException
from mcp.server import Server
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.ownership import (
    verify_lv_owner,
    verify_project_owner,
)
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.lv_template import LVTemplate
from app.db.models.project import Building, Floor, Project, Room, Unit
from app.db.session import async_session_factory
from app.db.models.user import User
from app.mcp.principal import get_current_user_id


logger = logging.getLogger(__name__)


# Process-singleton. The MCP SDK reads its handler tables off this
# instance — never replace it after import time.
server: Server = Server("baulv-mcp")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _default(obj: Any) -> Any:
    """``json.dumps`` default for the types our domain leaks.

    * ``UUID`` and ``Decimal`` get stringified so agents see stable
      values they can echo back as tool arguments without losing
      precision.
    * ``datetime`` becomes ISO-8601 — the canonical wire format.
    """
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        # str() over float() — float(Decimal("0.30")) → 0.3, which
        # changes the value silently. Agents don't care about the
        # extra zero, but the user might.
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _ok(payload: Any) -> list[mcp_types.TextContent]:
    """Standard happy-path return: pretty-printed JSON in a single block."""
    return [
        mcp_types.TextContent(
            type="text",
            text=json.dumps(payload, default=_default, indent=2, ensure_ascii=False),
        )
    ]


def _err(message: str) -> list[mcp_types.TextContent]:
    """Translate a human-readable error to MCP TextContent.

    We don't use ``isError`` here because the MCP SDK already wraps
    raised exceptions into the protocol-level error response. This
    helper is for cases where we *handle* the failure (e.g. an
    ownership 404) and want to give the agent something useful to
    show the user.
    """
    return [mcp_types.TextContent(type="text", text=f"Fehler: {message}")]


# ---------------------------------------------------------------------------
# Argument helpers — central so the schema stays consistent
# ---------------------------------------------------------------------------


def _require_uuid(arguments: dict | None, key: str) -> UUID:
    """Pull a UUID-shaped string out of ``arguments`` or raise ``ValueError``.

    The MCP SDK passes ``arguments`` as a plain dict deserialised from
    JSON, so even though we declare ``string`` + ``format: uuid`` in
    the input schema, validation is up to us.
    """
    if not arguments:
        raise ValueError(f"Argument fehlt: {key}")
    value = arguments.get(key)
    if not value:
        raise ValueError(f"Argument fehlt: {key}")
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Argument '{key}' ist keine gültige UUID: {value}") from exc


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


# We define the catalogue once and reuse it both in ``list_tools`` and
# the dispatcher's name-validation step. Order matters only for
# how clients display them; we go from broad to narrow.
_TOOLS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="list_projects",
        description=(
            "Listet alle Projekte des authentifizierten Users (id, name, "
            "kurze Metadaten). Ohne Argumente. Einstiegspunkt für jeden "
            "Agentenflow."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="get_project",
        description=(
            "Liefert die Stammdaten eines einzelnen Projekts (Adresse, "
            "Auftraggeber, Status, Zeitstempel). Erfordert die "
            "Projekt-UUID, die `list_projects` zurückgibt."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Projekts.",
                }
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="get_project_structure",
        description=(
            "Liefert den vollständigen Gebäudebaum eines Projekts: "
            "Buildings → Floors → Units → Rooms (mit Wand-/Deckenflächen "
            "und Öffnungs-Zählern). Ideal, um einem Agenten die Geometrie "
            "in einem einzigen Aufruf bereitzustellen."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Projekts.",
                }
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="list_rooms",
        description=(
            "Flache Liste aller Räume eines Projekts inklusive Geometrie "
            "und gecachter Wand-/Deckenflächen. Schneller als "
            "`get_project_structure`, wenn der Agent nur Räume vergleichen "
            "möchte."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Projekts.",
                }
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="list_lvs",
        description=(
            "Listet alle Leistungsverzeichnisse eines Projekts (id, name, "
            "Gewerk, Status, Anzahl Gruppen/Positionen). Keine Position-"
            "Details — dafür `get_lv` nutzen."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Projekts.",
                }
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="get_lv",
        description=(
            "Liefert ein Leistungsverzeichnis komplett mit Gruppen und "
            "Positionen (Kurz-/Langtext, Einheit, Menge, Einheitspreis, "
            "Gesperrt-Flag). Berechnungsnachweise sind nur als Anzahl "
            "enthalten — den Volltext liefert `get_position_with_proof`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "lv_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Leistungsverzeichnisses.",
                }
            },
            "required": ["lv_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="get_position_with_proof",
        description=(
            "Liefert eine einzelne Position mit allen "
            "Berechnungsnachweisen (Raum, Formel, ÖNORM-Faktor, "
            "Abzüge, Netto-Menge). Genau das, was der Agent braucht, "
            "um eine Mengenermittlung zu erklären oder zu prüfen."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "position_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID der Position.",
                }
            },
            "required": ["position_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="list_templates",
        description=(
            "Listet LV-Vorlagen, die der User sehen darf: alle "
            "System-Vorlagen plus die eigenen. Optional filterbar nach "
            "`category` (z. B. einfamilienhaus) oder `gewerk` (z. B. "
            "malerarbeiten). Liefert Zusammenfassungen mit "
            "Gruppen-/Positions-Anzahl, ohne den Positions-Volltext."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Optional: Kategorie-Filter (einfamilienhaus | "
                        "wohnanlage | buero | sanierung | dachausbau | "
                        "sonstiges)."
                    ),
                },
                "gewerk": {
                    "type": "string",
                    "description": (
                        "Optional: Gewerk-Filter (z. B. malerarbeiten)."
                    ),
                },
            },
            "additionalProperties": False,
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    """Standard MCP discovery endpoint — what can this server do?

    The catalogue is static; we just hand the module-level list back.
    """
    return list(_TOOLS)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _tool_list_projects(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Project)
            .where(Project.user_id == user.id)
            .order_by(Project.updated_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "address": p.address,
            "client_name": p.client_name,
            "project_number": p.project_number,
            "status": p.status,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in rows
    ]


async def _tool_get_project(
    db: AsyncSession, user: User, project_id: UUID
) -> dict[str, Any]:
    project = await verify_project_owner(project_id, user, db)
    return {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "address": project.address,
        "client_name": project.client_name,
        "project_number": project.project_number,
        "grundstuecksnr": project.grundstuecksnr,
        "planverfasser": project.planverfasser,
        "status": project.status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


async def _tool_get_project_structure(
    db: AsyncSession, user: User, project_id: UUID
) -> dict[str, Any]:
    """Return the full Building → Floor → Unit → Room tree.

    We eager-load all five levels so the serialiser never lazy-loads
    inside the async context (would raise ``MissingGreenlet``). Rooms
    carry the cached wall/ceiling figures — agents typically use this
    tool to answer "how many m² of wall in floor X" without doing
    follow-up calls.
    """
    await verify_project_owner(project_id, user, db)
    stmt = (
        select(Building)
        .where(Building.project_id == project_id)
        .options(
            selectinload(Building.floors)
            .selectinload(Floor.units)
            .selectinload(Unit.rooms)
            .selectinload(Room.openings),
        )
        .order_by(Building.sort_order, Building.name)
    )
    buildings = (await db.execute(stmt)).scalars().all()

    return {
        "project_id": str(project_id),
        "buildings": [
            {
                "id": str(b.id),
                "name": b.name,
                "sort_order": b.sort_order,
                "floors": [
                    {
                        "id": str(f.id),
                        "name": f.name,
                        "level_number": f.level_number,
                        "floor_height_m": f.floor_height_m,
                        "units": [
                            {
                                "id": str(u.id),
                                "name": u.name,
                                "unit_type": u.unit_type,
                                "rooms": [
                                    _room_summary(r) for r in sorted(
                                        u.rooms, key=lambda x: (x.name or "")
                                    )
                                ],
                            }
                            for u in sorted(
                                f.units, key=lambda x: x.sort_order
                            )
                        ],
                    }
                    for f in sorted(b.floors, key=lambda x: x.sort_order)
                ],
            }
            for b in buildings
        ],
    }


def _room_summary(room: Room) -> dict[str, Any]:
    """Common projection for ``get_project_structure`` and ``list_rooms``."""
    return {
        "id": str(room.id),
        "name": room.name,
        "room_number": room.room_number,
        "room_type": room.room_type,
        "area_m2": room.area_m2,
        "perimeter_m": room.perimeter_m,
        "height_m": room.height_m,
        "is_wet_room": room.is_wet_room,
        "is_staircase": room.is_staircase,
        "has_dachschraege": room.has_dachschraege,
        "ceiling_height_source": room.ceiling_height_source,
        "wall_area_gross_m2": room.wall_area_gross_m2,
        "wall_area_net_m2": room.wall_area_net_m2,
        "applied_factor": room.applied_factor,
        "deductions_enabled": room.deductions_enabled,
        # Counts only — full opening list would balloon the response.
        # Agents that need the openings can query the SPA endpoints.
        "opening_count": len(room.openings) if room.openings is not None else 0,
    }


async def _tool_list_rooms(
    db: AsyncSession, user: User, project_id: UUID
) -> dict[str, Any]:
    await verify_project_owner(project_id, user, db)
    stmt = (
        select(Room)
        .join(Unit).join(Floor).join(Building)
        .where(Building.project_id == project_id)
        .options(selectinload(Room.openings))
        .order_by(Room.name)
    )
    rooms = (await db.execute(stmt)).scalars().all()
    return {
        "project_id": str(project_id),
        "room_count": len(rooms),
        "rooms": [_room_summary(r) for r in rooms],
    }


async def _tool_list_lvs(
    db: AsyncSession, user: User, project_id: UUID
) -> dict[str, Any]:
    await verify_project_owner(project_id, user, db)
    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.project_id == project_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen)
            .selectinload(Leistungsgruppe.positionen),
        )
        .order_by(Leistungsverzeichnis.created_at.desc())
    )
    lvs = (await db.execute(stmt)).scalars().all()
    return {
        "project_id": str(project_id),
        "lvs": [
            {
                "id": str(lv.id),
                "name": lv.name,
                "trade": lv.trade,
                "status": lv.status,
                "created_at": lv.created_at,
                "updated_at": lv.updated_at,
                "gruppen_count": len(lv.gruppen),
                "positionen_count": sum(len(g.positionen) for g in lv.gruppen),
            }
            for lv in lvs
        ],
    }


async def _tool_get_lv(
    db: AsyncSession, user: User, lv_id: UUID
) -> dict[str, Any]:
    await verify_lv_owner(lv_id, user, db)
    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen)
            .selectinload(Leistungsgruppe.positionen)
            .selectinload(Position.berechnungsnachweise),
        )
    )
    lv = (await db.execute(stmt)).scalars().first()
    if lv is None:
        raise HTTPException(404, "LV nicht gefunden")
    return {
        "id": str(lv.id),
        "project_id": str(lv.project_id),
        "name": lv.name,
        "trade": lv.trade,
        "status": lv.status,
        "vorbemerkungen": lv.vorbemerkungen,
        "created_at": lv.created_at,
        "updated_at": lv.updated_at,
        "gruppen": [
            {
                "id": str(g.id),
                "nummer": g.nummer,
                "bezeichnung": g.bezeichnung,
                "sort_order": g.sort_order,
                "created_at": g.created_at,
                "updated_at": g.updated_at,
                "positionen": [
                    {
                        "id": str(p.id),
                        "positions_nummer": p.positions_nummer,
                        "kurztext": p.kurztext,
                        "langtext": p.langtext,
                        "einheit": p.einheit,
                        "menge": p.menge,
                        "einheitspreis": p.einheitspreis,
                        "gesamtpreis": p.gesamtpreis,
                        "positionsart": p.positionsart,
                        "text_source": p.text_source,
                        "is_locked": p.is_locked,
                        "sort_order": p.sort_order,
                        "created_at": p.created_at,
                        "updated_at": p.updated_at,
                        # Counts only here; full proof via
                        # `get_position_with_proof`.
                        "berechnungsnachweis_count": len(p.berechnungsnachweise),
                    }
                    for p in sorted(g.positionen, key=lambda x: x.sort_order)
                ],
            }
            for g in sorted(lv.gruppen, key=lambda x: x.sort_order)
        ],
    }


async def _tool_get_position_with_proof(
    db: AsyncSession, user: User, position_id: UUID
) -> dict[str, Any]:
    """Fetch a position + every Berechnungsnachweis attached to it.

    We walk the ownership chain manually (Position → Gruppe → LV →
    Project → User) because there's no ``verify_position_owner`` helper
    yet. The 404-mask lives here too — a non-owner sees the same
    "not found" as a real miss.
    """
    stmt = (
        select(Position)
        .where(Position.id == position_id)
        .options(
            selectinload(Position.berechnungsnachweise)
            .selectinload(Berechnungsnachweis.room),
        )
    )
    position = (await db.execute(stmt)).scalars().first()
    if position is None:
        raise HTTPException(404, "Position nicht gefunden")

    gruppe = await db.get(Leistungsgruppe, position.gruppe_id)
    if gruppe is None:
        # Orphan — would only happen with a corrupt DB. 404-mask.
        raise HTTPException(404, "Position nicht gefunden")
    # ``verify_lv_owner`` enforces user ownership and 404-masks across
    # tenants. Reusing it keeps the rule in one place.
    await verify_lv_owner(gruppe.lv_id, user, db)

    return {
        "id": str(position.id),
        "gruppe_id": str(position.gruppe_id),
        "positions_nummer": position.positions_nummer,
        "kurztext": position.kurztext,
        "langtext": position.langtext,
        "einheit": position.einheit,
        "menge": position.menge,
        "einheitspreis": position.einheitspreis,
        "gesamtpreis": position.gesamtpreis,
        "positionsart": position.positionsart,
        "text_source": position.text_source,
        "is_locked": position.is_locked,
        "berechnungsnachweise": [
            {
                "id": str(b.id),
                "room_id": str(b.room_id),
                "room_name": b.room.name if b.room else None,
                "raw_quantity": b.raw_quantity,
                "formula_description": b.formula_description,
                "formula_expression": b.formula_expression,
                "onorm_factor": b.onorm_factor,
                "onorm_rule_ref": b.onorm_rule_ref,
                "onorm_paragraph": b.onorm_paragraph,
                "deductions": b.deductions,
                "net_quantity": b.net_quantity,
                "unit": b.unit,
                "notes": b.notes,
                "created_at": b.created_at,
            }
            for b in position.berechnungsnachweise
        ],
    }


async def _tool_list_templates(
    db: AsyncSession,
    user: User,
    category: str | None,
    gewerk: str | None,
) -> dict[str, Any]:
    """Return system + own templates, optionally filtered.

    Mirrors the visibility rule in ``app/api/templates.py``: a user
    sees every system template plus the ones they themselves created.
    Other users' custom templates are never exposed.
    """
    from sqlalchemy import or_

    stmt = select(LVTemplate).where(
        or_(
            LVTemplate.is_system.is_(True),
            LVTemplate.created_by_user_id == user.id,
        )
    )
    if category:
        stmt = stmt.where(LVTemplate.category == category)
    if gewerk:
        stmt = stmt.where(LVTemplate.gewerk == gewerk)
    stmt = stmt.order_by(
        LVTemplate.is_system.desc(), LVTemplate.created_at.desc()
    )
    rows = (await db.execute(stmt)).scalars().all()

    def _counts(payload: dict | None) -> tuple[int, int]:
        if not isinstance(payload, dict):
            return 0, 0
        gruppen = payload.get("gruppen") or []
        return (
            len(gruppen),
            sum(
                len(g.get("positionen") or [])
                for g in gruppen
                if isinstance(g, dict)
            ),
        )

    out: list[dict[str, Any]] = []
    for tpl in rows:
        gc, pc = _counts(tpl.template_data)
        out.append(
            {
                "id": str(tpl.id),
                "name": tpl.name,
                "description": tpl.description,
                "category": tpl.category,
                "gewerk": tpl.gewerk,
                "is_system": tpl.is_system,
                "created_by_user_id": (
                    str(tpl.created_by_user_id)
                    if tpl.created_by_user_id
                    else None
                ),
                "created_at": tpl.created_at,
                "updated_at": tpl.updated_at,
                "gruppen_count": gc,
                "positionen_count": pc,
            }
        )
    return {"templates": out}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@server.call_tool()
async def call_tool(
    name: str, arguments: dict | None
) -> list[mcp_types.TextContent]:
    """Single entrypoint for every tool invocation.

    The MCP SDK calls this with the tool ``name`` and the JSON
    ``arguments`` dict. We resolve the principal, open a DB session,
    and dispatch to the right handler. Errors get translated into
    user-readable German text — agents pass that through to the user
    directly, which is the right UX for an MVP read-only surface.
    """
    user_id = get_current_user_id()
    logger.info("mcp.call_tool name=%s user_id=%s", name, user_id)

    async with async_session_factory() as db:
        try:
            user = await db.get(User, user_id)
            if user is None:
                # Token resolved at handshake but the user has since
                # been deleted. Treat as auth failure.
                return _err("Benutzer nicht mehr verfügbar.")

            if name == "list_projects":
                return _ok(await _tool_list_projects(db, user))

            if name == "get_project":
                project_id = _require_uuid(arguments, "project_id")
                return _ok(await _tool_get_project(db, user, project_id))

            if name == "get_project_structure":
                project_id = _require_uuid(arguments, "project_id")
                return _ok(
                    await _tool_get_project_structure(db, user, project_id)
                )

            if name == "list_rooms":
                project_id = _require_uuid(arguments, "project_id")
                return _ok(await _tool_list_rooms(db, user, project_id))

            if name == "list_lvs":
                project_id = _require_uuid(arguments, "project_id")
                return _ok(await _tool_list_lvs(db, user, project_id))

            if name == "get_lv":
                lv_id = _require_uuid(arguments, "lv_id")
                return _ok(await _tool_get_lv(db, user, lv_id))

            if name == "get_position_with_proof":
                position_id = _require_uuid(arguments, "position_id")
                return _ok(
                    await _tool_get_position_with_proof(db, user, position_id)
                )

            if name == "list_templates":
                args = arguments or {}
                return _ok(
                    await _tool_list_templates(
                        db,
                        user,
                        category=args.get("category"),
                        gewerk=args.get("gewerk"),
                    )
                )

            return _err(f"Unbekanntes Tool: {name}")

        except ValueError as exc:
            # Argument validation failures from ``_require_uuid`` —
            # the user/agent gave us a bad UUID. Return a clean
            # message so the agent can reformulate the call.
            return _err(str(exc))
        except HTTPException as exc:
            # Ownership / 404-mask path. Translate to plain text;
            # don't leak the FastAPI exception type to the MCP client.
            return _err(str(exc.detail))
        except Exception:
            # Defensive: a tool blowing up shouldn't crash the SSE
            # stream. We log the traceback and return an opaque
            # apology — the tool call surfaces as an error result,
            # the SSE connection stays open, the agent can retry.
            logger.exception(
                "mcp.call_tool_failed name=%s user_id=%s", name, user_id
            )
            return _err(
                "Interner Fehler beim Verarbeiten dieses Tools. "
                "Der Vorfall wurde protokolliert."
            )
