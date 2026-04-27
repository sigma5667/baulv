"""The MCP ``Server`` instance and the tools we expose to headless agents.

3a shipped read-only — list projects, walk the building tree, dump an
LV. 3b adds the mutation surface so an agent can run the typical
"Projekt anlegen → LV aus Vorlage → Preise tunen" flow end-to-end.

Tool taxonomy
=============

Read-only (3a):

* ``list_projects``, ``get_project``, ``get_project_structure``,
  ``list_rooms``, ``list_lvs``, ``get_lv``,
  ``get_position_with_proof``, ``list_templates``

Mutations (3b):

* ``create_project``  — POST /api/projects/   (plan-limited)
* ``update_project``  — PUT /api/projects/{id}
* ``create_lv``       — POST /api/projects/{id}/lv
* ``create_lv_from_template`` — POST /api/lv/from-template
* ``update_lv``       — PUT /api/lv/{id}      (metadata only)
* ``update_position`` — PUT /api/positionen/{id} (with stricter
                         is_locked semantics — see handler docstring)
* ``create_template_from_lv`` — POST /api/templates

What we deliberately do *not* expose yet
========================================

* Building / Floor / Unit / Room CRUD — better authored in the SPA
  where the user can preview the structure.
* Deletes — large blast radius, want a separate confirmation pattern
  before letting agents wipe rows.
* Calculate / sync-walls / generate-texts — ``update_*`` tools and
  the implicit auto-recalc on the SPA side cover the common workflow.

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
3. Reuses the existing ``app.api.ownership`` helpers AND the existing
   Pydantic schemas (``ProjectCreate``, ``LVUpdate``, …) so the
   validation rules and the tenancy model are identical to what the
   SPA endpoints enforce. If validation or ownership fails the helper
   raises ``HTTPException`` / ``ValidationError`` / ``ValueError``;
   we translate them into textual errors so the MCP client sees
   readable German messages instead of stack traces.
4. For mutations: commits the session before returning, so the
   write actually lands on disk before the agent sees the success
   payload. (Read tools never write so no commit is required.)
5. Returns ``[TextContent(text=...)]`` containing JSON. JSON is the
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
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.lv import _copy_template_payload_into_new_lv
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
from app.schemas.lv import LVCreate, LVUpdate, PositionUpdate
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.schemas.template import LVFromTemplateRequest, TemplateCreateFromLV
from app.subscriptions import check_project_limit


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
    # -----------------------------------------------------------------
    # Mutation tools (3b). Same validation rules and tenancy checks as
    # the REST endpoints they mirror. Plan-limits are enforced via
    # ``check_project_limit`` (which honours the beta-unlock flag), so
    # nothing extra is needed for tester runs.
    # -----------------------------------------------------------------
    mcp_types.Tool(
        name="create_project",
        description=(
            "Legt ein neues Projekt für den authentifizierten User an. "
            "Pflichtfeld ist der Name; alle weiteren Felder (Adresse, "
            "Auftraggeber, Projektnummer, Grundstücksnummer, "
            "Planverfasser, Beschreibung) sind optional. Respektiert das "
            "Projekt-Limit des aktuellen Plans (Basis: 3 Projekte; Pro/"
            "Enterprise: unbegrenzt). Bei aktiver Beta-Freischaltung "
            "entfällt das Limit für alle User. Antwortet mit dem "
            "vollständigen Projekt-Datensatz inkl. id."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Pflicht. Anzeigename des Projekts, z. B. "
                        "'EFH Schmidt 2026'."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Freie Projektbeschreibung.",
                },
                "address": {
                    "type": "string",
                    "description": (
                        "Bauadresse, z. B. 'Hauptstraße 12, 4020 Linz'."
                    ),
                },
                "client_name": {
                    "type": "string",
                    "description": "Auftraggeber / Bauherr.",
                },
                "project_number": {
                    "type": "string",
                    "description": "Interne Projektnummer.",
                },
                "grundstuecksnr": {
                    "type": "string",
                    "description": "Grundstücksnummer (z. B. KG/EZ).",
                },
                "planverfasser": {
                    "type": "string",
                    "description": "Planverfasser / Architekt.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="update_project",
        description=(
            "Aktualisiert ein bestehendes Projekt (Patch-Semantik: nur "
            "übergebene Felder werden geändert, fehlende bleiben "
            "unangetastet). Eigentum wird über `verify_project_owner` "
            "geprüft — User können fremde Projekte nicht modifizieren. "
            "Beispiel: nur den Status auf 'archiviert' setzen → "
            "{project_id, status: 'archiviert'}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des zu ändernden Projekts.",
                },
                "name": {"type": "string"},
                "description": {"type": "string"},
                "address": {"type": "string"},
                "client_name": {"type": "string"},
                "project_number": {"type": "string"},
                "grundstuecksnr": {"type": "string"},
                "planverfasser": {"type": "string"},
                "status": {
                    "type": "string",
                    "description": (
                        "Projekt-Status, z. B. 'aktiv', 'archiviert', "
                        "'abgeschlossen'."
                    ),
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="create_lv",
        description=(
            "Legt ein leeres Leistungsverzeichnis innerhalb eines "
            "Projekts an. Für den Standard-Flow 'LV aus Vorlage' gibt "
            "es das separate Tool `create_lv_from_template` — dieses "
            "Tool ist der manuelle Pfad ohne Positionen. "
            "Pflichtfelder: project_id, name, trade. "
            "Beispiel: {project_id, name: 'Malerarbeiten OG', "
            "trade: 'malerarbeiten'}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Ziel-Projekts.",
                },
                "name": {
                    "type": "string",
                    "description": "Anzeigename des LVs.",
                },
                "trade": {
                    "type": "string",
                    "description": (
                        "Gewerk-Slug, z. B. 'malerarbeiten', "
                        "'tapezierarbeiten', 'fliesenlegearbeiten'."
                    ),
                },
                "vorbemerkungen": {
                    "type": "string",
                    "description": "Optionaler Vorbemerkungstext.",
                },
            },
            "required": ["project_id", "name", "trade"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="create_lv_from_template",
        description=(
            "Standard-Flow für Agents: kopiert eine LV-Vorlage in ein "
            "Projekt. Das Tool legt ein neues Leistungsverzeichnis an, "
            "kopiert alle Gruppen und Positionen aus der Vorlage und "
            "lässt Mengen + Einheitspreise leer (die füllt der User "
            "oder die Berechnungsmaschine). Vorlagen-Sichtbarkeit: "
            "System-Vorlagen sind für alle, eigene Vorlagen nur für "
            "den Ersteller. Beispiel: {project_id, template_id, "
            "name: 'Malerarbeiten EG'} (name optional, default = "
            "Vorlagenname)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Ziel-Projekts.",
                },
                "template_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": (
                        "UUID der Vorlage (z. B. aus `list_templates`)."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Optional: abweichender Name für das neue LV. "
                        "Default: Name der Vorlage."
                    ),
                },
            },
            "required": ["project_id", "template_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="update_lv",
        description=(
            "Aktualisiert die Metadaten eines Leistungsverzeichnisses "
            "(Name, Status, Vorbemerkungen). Positionen oder Gruppen "
            "können hier nicht geändert werden — dafür gibt es "
            "`update_position`. Patch-Semantik: nur übergebene Felder "
            "werden geschrieben."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "lv_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des LVs.",
                },
                "name": {"type": "string"},
                "status": {
                    "type": "string",
                    "description": (
                        "Status, z. B. 'entwurf', 'final', 'archiviert'."
                    ),
                },
                "vorbemerkungen": {"type": "string"},
            },
            "required": ["lv_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="update_position",
        description=(
            "Aktualisiert eine einzelne Position (Kurztext, Langtext, "
            "Einheitspreis, Sperr-Flag). Nicht änderbar via MCP: "
            "Menge, Einheit, Positions-Nummer, Gruppen-Zuordnung. "
            "Wichtige Sperr-Regel: ist eine Position aktuell gesperrt "
            "(`is_locked = true`), kann sie via MCP NUR durch ein "
            "Patch `{is_locked: false}` allein entsperrt werden — "
            "weitere Felder im selben Aufruf werden mit 403 abgewiesen. "
            "Das erzwingt einen sauberen 'erst entsperren, dann ändern'-"
            "Workflow für Audit-Zwecke. Wenn `kurztext` oder `langtext` "
            "gesetzt werden, wird `text_source` automatisch auf "
            "'manual' gesetzt — analog zum SPA-Verhalten."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "position_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID der Position.",
                },
                "kurztext": {"type": "string"},
                "langtext": {"type": "string"},
                "einheitspreis": {
                    "type": "number",
                    "description": (
                        "Einheitspreis in Euro, z. B. 12.50."
                    ),
                },
                "is_locked": {
                    "type": "boolean",
                    "description": (
                        "Sperrt die Position vor weiteren Mutationen "
                        "(true) oder gibt sie wieder frei (false)."
                    ),
                },
            },
            "required": ["position_id"],
            "additionalProperties": False,
        },
    ),
    mcp_types.Tool(
        name="create_template_from_lv",
        description=(
            "Speichert ein bestehendes LV als wiederverwendbare "
            "Vorlage des Users. Die Gruppen-/Positions-Struktur wird "
            "in `template_data` kopiert; Mengen und Einheitspreise "
            "werden bewusst NICHT übernommen — Vorlagen sind preis- "
            "und mengenagnostisch. Pflichtfelder: lv_id, name, "
            "category."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "lv_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "UUID des Quell-LVs.",
                },
                "name": {
                    "type": "string",
                    "description": "Anzeigename der Vorlage.",
                },
                "description": {
                    "type": "string",
                    "description": "Optionale Vorlagen-Beschreibung.",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "einfamilienhaus",
                        "wohnanlage",
                        "buero",
                        "sanierung",
                        "dachausbau",
                        "sonstiges",
                    ],
                    "description": "Vorlagen-Kategorie.",
                },
            },
            "required": ["lv_id", "name", "category"],
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
# Shared projections — keep create/update/get tools shape-consistent
# ---------------------------------------------------------------------------


def _project_to_dict(project: Project) -> dict[str, Any]:
    """Project projection used by ``get_project``, ``create_project``,
    and ``update_project`` so the three tools return the same fields
    in the same order. Agents that introspect the response key set
    don't have to special-case which surface they came from.
    """
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


def _lv_metadata_to_dict(lv: Leistungsverzeichnis) -> dict[str, Any]:
    """LV metadata-only projection (no eager-loaded gruppen) used by
    ``create_lv`` and ``update_lv``. ``get_lv`` uses its own deeper
    projection because it ships the full tree.
    """
    return {
        "id": str(lv.id),
        "project_id": str(lv.project_id),
        "name": lv.name,
        "trade": lv.trade,
        "status": lv.status,
        "vorbemerkungen": lv.vorbemerkungen,
        "created_at": lv.created_at,
        "updated_at": lv.updated_at,
    }


def _template_summary_to_dict(
    tpl: LVTemplate, gruppen_count: int, positionen_count: int
) -> dict[str, Any]:
    """Mirror the shape ``list_templates`` produces so a freshly
    created template can be displayed by the same agent code that
    iterates the list response."""
    return {
        "id": str(tpl.id),
        "name": tpl.name,
        "description": tpl.description,
        "category": tpl.category,
        "gewerk": tpl.gewerk,
        "is_system": tpl.is_system,
        "created_by_user_id": (
            str(tpl.created_by_user_id) if tpl.created_by_user_id else None
        ),
        "created_at": tpl.created_at,
        "updated_at": tpl.updated_at,
        "gruppen_count": gruppen_count,
        "positionen_count": positionen_count,
    }


# ---------------------------------------------------------------------------
# Mutation tool implementations (3b)
# ---------------------------------------------------------------------------
#
# Each mutation tool:
#
# * Parses raw arguments through the existing Pydantic schema so the
#   validation rules are identical to the REST endpoint's. A
#   ``ValidationError`` here propagates up to the dispatcher, which
#   translates it into a German "Ungültige Argumente" message.
# * Enforces tenancy via ``verify_project_owner`` / ``verify_lv_owner``.
# * Calls ``await db.commit()`` at the end. Read tools don't commit
#   because there's nothing to write; mutation tools must, since the
#   session is opened by the dispatcher's ``async with`` and would
#   otherwise be rolled back when the block exits.
#
# The whole story for "no drift between REST and MCP": same Pydantic
# input schemas, same ownership helpers, same plan-limit helper, and
# for the only complex copy logic (template → LV) the same shared
# helper ``_copy_template_payload_into_new_lv`` from ``app.api.lv``.


async def _tool_create_project(
    db: AsyncSession, user: User, arguments: dict | None
) -> dict[str, Any]:
    """Create a project for the calling user.

    Mirrors ``POST /api/projects/`` including the plan-based project
    limit check. ``check_project_limit`` already honours the
    ``BETA_UNLOCK_ALL_FEATURES`` flag — so during tester runs Basis
    users can create unlimited projects via MCP just like via the
    SPA.
    """
    args = arguments or {}
    data = ProjectCreate(**args)

    # Same plan-limit dance the REST endpoint does. Beta unlock is
    # honoured implicitly by ``check_project_limit``.
    count_result = await db.execute(
        select(func.count(Project.id)).where(Project.user_id == user.id)
    )
    current_count = count_result.scalar() or 0
    if not check_project_limit(user.subscription_plan, current_count):
        raise HTTPException(
            403,
            f"Projektlimit erreicht. Ihr {user.subscription_plan.title()}-"
            f"Plan erlaubt maximal {current_count} Projekte. Bitte "
            f"upgraden Sie Ihr Abonnement.",
        )

    project = Project(user_id=user.id, **data.model_dump())
    db.add(project)
    await db.commit()
    await db.refresh(project)
    logger.info(
        "mcp.create_project user_id=%s project_id=%s name=%r",
        user.id, project.id, project.name,
    )
    return _project_to_dict(project)


async def _tool_update_project(
    db: AsyncSession, user: User, project_id: UUID, arguments: dict | None
) -> dict[str, Any]:
    """Patch a project's metadata. Same patch-semantics as the REST
    endpoint: only fields that are present in ``arguments`` get
    written; fields the agent omits stay untouched."""
    project = await verify_project_owner(project_id, user, db)
    patch_fields = {
        k: v for k, v in (arguments or {}).items() if k != "project_id"
    }
    patch = ProjectUpdate(**patch_fields)
    for key, value in patch.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    await db.commit()
    await db.refresh(project)
    logger.info(
        "mcp.update_project user_id=%s project_id=%s fields=%s",
        user.id, project.id, sorted(patch.model_dump(exclude_unset=True).keys()),
    )
    return _project_to_dict(project)


async def _tool_create_lv(
    db: AsyncSession, user: User, arguments: dict | None
) -> dict[str, Any]:
    """Create an empty Leistungsverzeichnis inside a project.

    For the typical "LV aus Vorlage" flow agents should reach for
    ``create_lv_from_template`` instead; this is the manual path
    (no positions, agent fills them in afterwards via ``update_position``
    once the SPA-side calculation engine has populated them, or via
    a follow-up ``create_lv_from_template`` later).
    """
    args = arguments or {}
    project_id = _require_uuid(args, "project_id")
    await verify_project_owner(project_id, user, db)

    payload = {k: v for k, v in args.items() if k != "project_id"}
    data = LVCreate(**payload)

    lv = Leistungsverzeichnis(project_id=project_id, **data.model_dump())
    db.add(lv)
    await db.commit()
    await db.refresh(lv)
    logger.info(
        "mcp.create_lv user_id=%s project_id=%s lv_id=%s name=%r trade=%r",
        user.id, project_id, lv.id, lv.name, lv.trade,
    )
    return _lv_metadata_to_dict(lv)


async def _tool_create_lv_from_template(
    db: AsyncSession, user: User, arguments: dict | None
) -> dict[str, Any]:
    """Spawn a new LV from a template. Reuses the exact same helper
    (``_copy_template_payload_into_new_lv``) as the REST endpoint so
    there is one source of truth for the copy semantics — that's the
    "kein Drift zwischen REST und MCP" guarantee in concrete code.
    """
    args = arguments or {}
    data = LVFromTemplateRequest(**args)

    await verify_project_owner(data.project_id, user, db)
    tpl = await db.get(LVTemplate, data.template_id)
    if not tpl:
        raise HTTPException(404, "Vorlage nicht gefunden")
    if not tpl.is_system and tpl.created_by_user_id != user.id:
        # 404-mask across tenants — same as the REST endpoint.
        raise HTTPException(404, "Vorlage nicht gefunden")

    lv, gruppen_created, positionen_created = (
        await _copy_template_payload_into_new_lv(
            tpl, data.project_id, data.name, db
        )
    )
    await db.commit()
    logger.info(
        "mcp.create_lv_from_template user_id=%s project_id=%s "
        "template_id=%s lv_id=%s gruppen=%d positionen=%d",
        user.id, data.project_id, data.template_id, lv.id,
        gruppen_created, positionen_created,
    )
    return {
        "lv_id": str(lv.id),
        "project_id": str(lv.project_id),
        "name": lv.name,
        "trade": lv.trade,
        "gruppen_created": gruppen_created,
        "positionen_created": positionen_created,
    }


async def _tool_update_lv(
    db: AsyncSession, user: User, lv_id: UUID, arguments: dict | None
) -> dict[str, Any]:
    """Patch LV metadata (name, status, Vorbemerkungen). Same
    patch-semantics as ``update_project``."""
    lv = await verify_lv_owner(lv_id, user, db)
    patch_fields = {
        k: v for k, v in (arguments or {}).items() if k != "lv_id"
    }
    patch = LVUpdate(**patch_fields)
    for key, value in patch.model_dump(exclude_unset=True).items():
        setattr(lv, key, value)
    await db.commit()
    await db.refresh(lv)
    logger.info(
        "mcp.update_lv user_id=%s lv_id=%s fields=%s",
        user.id, lv.id, sorted(patch.model_dump(exclude_unset=True).keys()),
    )
    return _lv_metadata_to_dict(lv)


async def _tool_update_position(
    db: AsyncSession, user: User, position_id: UUID, arguments: dict | None
) -> dict[str, Any]:
    """Patch a single Position via the LV-owner chain.

    Stricter than the REST endpoint by design: the SPA's
    ``PUT /api/positionen/{id}`` allows blind overwrites of locked
    positions (a human user sees the lock icon on screen and won't
    typically do that). An agent has no such visual cue, so we
    enforce a hard rule:

    * If the position is currently ``is_locked = True``, the only
      patch we accept is ``{is_locked: false}`` *alone*. Any other
      field present in the same call returns 403 with a German
      error message that an agent can echo back to the user.
    * That forces a clean two-step flow: unlock first, then mutate.
      Both writes show up as separate calls in the SSE log so an
      audit trail (planned for 3c) can attribute each step
      individually.
    """
    position = (
        await db.execute(
            select(Position).where(Position.id == position_id)
        )
    ).scalars().first()
    if position is None:
        raise HTTPException(404, "Position nicht gefunden")

    gruppe = await db.get(Leistungsgruppe, position.gruppe_id)
    if gruppe is None:  # pragma: no cover — corrupt-DB defensive
        raise HTTPException(404, "Position nicht gefunden")
    await verify_lv_owner(gruppe.lv_id, user, db)

    patch_fields = {
        k: v for k, v in (arguments or {}).items() if k != "position_id"
    }
    patch = PositionUpdate(**patch_fields)
    set_fields = patch.model_dump(exclude_unset=True)

    # Lock guard. We treat "currently locked" as the gate, not "ends
    # up locked" — an agent that wants to make a textual edit on a
    # locked row must explicitly unlock first.
    if position.is_locked:
        if set_fields != {"is_locked": False}:
            # Anything other than the bare unlock is rejected. We
            # surface this as 403 (not 400) because the request is
            # well-formed but the resource is in a state that
            # forbids the operation.
            raise HTTPException(
                403,
                "Position ist gesperrt. Erst entsperren mit "
                "{is_locked: false}, dann in einem zweiten Aufruf "
                "Texte oder Preise ändern.",
            )

    for key, value in set_fields.items():
        setattr(position, key, value)
    # Mirror the REST endpoint: any text change flips text_source to
    # 'manual' so downstream tooling (template-saving, AI-text re-gen)
    # knows the agent — not the template or generator — wrote this.
    if patch.kurztext is not None or patch.langtext is not None:
        position.text_source = "manual"

    await db.commit()
    await db.refresh(position)
    logger.info(
        "mcp.update_position user_id=%s position_id=%s fields=%s "
        "is_locked=%s",
        user.id, position.id, sorted(set_fields.keys()), position.is_locked,
    )
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
        "sort_order": position.sort_order,
    }


async def _tool_create_template_from_lv(
    db: AsyncSession, user: User, arguments: dict | None
) -> dict[str, Any]:
    """Save an LV as a user-owned template.

    Mirrors ``POST /api/templates``. Mengen + Einheitspreise are
    stripped (templates are price- and quantity-agnostic by design —
    those numbers belong to the concrete project the template is
    later applied to).
    """
    args = arguments or {}
    data = TemplateCreateFromLV(**args)

    await verify_lv_owner(data.lv_id, user, db)
    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == data.lv_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen)
            .selectinload(Leistungsgruppe.positionen)
        )
    )
    lv = (await db.execute(stmt)).scalars().first()
    if lv is None:  # pragma: no cover — verify_lv_owner already checked
        raise HTTPException(404, "LV nicht gefunden")

    # Build template_data — same shape the seeded system templates
    # use and the same projection ``app.api.templates`` writes. We
    # intentionally keep this small block in lock-step with the REST
    # version; if either ever changes, both should.
    gruppen_payload: list[dict[str, Any]] = []
    for gruppe in sorted(lv.gruppen, key=lambda g: g.sort_order):
        positionen_payload: list[dict[str, Any]] = []
        for pos in sorted(gruppe.positionen, key=lambda p: p.sort_order):
            positionen_payload.append(
                {
                    "positions_nummer": pos.positions_nummer,
                    "kurztext": pos.kurztext,
                    "langtext": pos.langtext,
                    "einheit": pos.einheit,
                    "kategorie": None,
                }
            )
        gruppen_payload.append(
            {
                "nummer": gruppe.nummer,
                "bezeichnung": gruppe.bezeichnung,
                "positionen": positionen_payload,
            }
        )

    if not gruppen_payload:
        raise HTTPException(
            400,
            "Das LV ist leer — es gibt keine Positionen, die in eine "
            "Vorlage übernommen werden könnten.",
        )

    tpl = LVTemplate(
        name=data.name,
        description=data.description,
        category=data.category,
        gewerk=lv.trade,
        is_system=False,
        created_by_user_id=user.id,
        template_data={"gruppen": gruppen_payload},
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    positionen_total = sum(len(g["positionen"]) for g in gruppen_payload)
    logger.info(
        "mcp.create_template_from_lv user_id=%s lv_id=%s template_id=%s "
        "gruppen=%d positionen=%d",
        user.id, data.lv_id, tpl.id, len(gruppen_payload), positionen_total,
    )
    return _template_summary_to_dict(
        tpl, gruppen_count=len(gruppen_payload), positionen_count=positionen_total
    )


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

            # ------------------------------------------------------
            # Mutation tools (3b). Each branch defers all validation
            # to the handler — Pydantic-level errors come back as
            # ``ValidationError`` and are caught below; ownership
            # failures come back as ``HTTPException`` from
            # ``verify_*_owner`` and are also caught below.
            # ------------------------------------------------------

            if name == "create_project":
                return _ok(await _tool_create_project(db, user, arguments))

            if name == "update_project":
                project_id = _require_uuid(arguments, "project_id")
                return _ok(
                    await _tool_update_project(db, user, project_id, arguments)
                )

            if name == "create_lv":
                return _ok(await _tool_create_lv(db, user, arguments))

            if name == "create_lv_from_template":
                return _ok(
                    await _tool_create_lv_from_template(db, user, arguments)
                )

            if name == "update_lv":
                lv_id = _require_uuid(arguments, "lv_id")
                return _ok(await _tool_update_lv(db, user, lv_id, arguments))

            if name == "update_position":
                position_id = _require_uuid(arguments, "position_id")
                return _ok(
                    await _tool_update_position(
                        db, user, position_id, arguments
                    )
                )

            if name == "create_template_from_lv":
                return _ok(
                    await _tool_create_template_from_lv(db, user, arguments)
                )

            return _err(f"Unbekanntes Tool: {name}")

        except ValueError as exc:
            # Argument validation failures from ``_require_uuid`` —
            # the user/agent gave us a bad UUID. Return a clean
            # message so the agent can reformulate the call.
            return _err(str(exc))
        except ValidationError as exc:
            # Pydantic v2 ``ValidationError`` does NOT inherit from
            # ``ValueError``, so we need an explicit branch here. We
            # surface the *first* error message — agents respond
            # better to a single concrete complaint ("name: Field
            # required") than to a multi-error JSON dump.
            errors = exc.errors()
            if errors:
                first = errors[0]
                loc = ".".join(str(part) for part in first.get("loc", ()))
                msg = first.get("msg", "Ungültige Argumente")
                detail = f"{loc}: {msg}" if loc else msg
            else:  # pragma: no cover — defensive fallback
                detail = "Ungültige Argumente"
            return _err(f"Ungültige Argumente: {detail}")
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
