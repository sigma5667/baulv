"""DSGVO (GDPR) compliance helpers.

This module implements the two data-subject rights that require the most
machinery on our side:

* **Art. 20 DSGVO — Right to data portability.** ``export_user_data``
  builds a single JSON-serializable dictionary containing every row in
  the database that belongs to the calling user. The format is plain JSON
  (a "structured, commonly used and machine-readable format" as required
  by Art. 20 (1)). Binary artefacts such as the uploaded PDFs of building
  plans are *not* embedded — they are listed by filename and size so the
  user can separately request them, and so the export stays small enough
  to ship in a single response.

* **Art. 17 DSGVO — Right to erasure.** ``delete_user_account`` performs
  the irreversible purge:

  1. Cancel any live Stripe subscription (best-effort; failures are
     logged but do not block deletion — the user's right to erasure
     takes precedence over billing hygiene).
  2. Delete plan PDF files from local storage. The on-disk layout is
     ``upload_path/<project_id>/...`` so we remove the per-project
     directory for each of the user's projects.
  3. Delete the ``users`` row. Every user-scoped table has an ON DELETE
     CASCADE FK chain rooted at ``projects.user_id``, so a single row
     delete tears down projects, plans, buildings, floors, units, rooms,
     openings, LVs, groups, positions, calculation proofs, and chat
     sessions/messages in one statement.

  Legacy reference-library tables (``onorm_dokumente``,
  ``onorm_regeln``) — historical schema, both tables were dropped
  in migrations 008 and 010 — were shared non-personal data and
  would be out of scope here regardless. The ``onorm_*`` column
  names that remain on ``berechnungsnachweise`` are aliased to
  neutral public names at the API layer (see ``schemas/lv.py``).
"""

from __future__ import annotations

import logging
import shutil
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models.audit import AuditLogEntry
from app.db.models.calculation import Berechnungsnachweis
from app.db.models.chat import ChatMessage, ChatSession
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position
from app.db.models.plan import Plan
from app.db.models.project import (
    Building,
    Floor,
    Opening,
    Project,
    Room,
    Unit,
)
from app.db.models.analytics import UsageAnalyticsEvent
from app.db.models.api_key import ApiKey
from app.db.models.consent import ConsentSnapshot
from app.db.models.lv_template import LVTemplate
from app.db.models.mcp_audit import McpAuditLogEntry
from app.db.models.session import UserSession
from app.db.models.user import User
from app.db.models.waitlist_entry import WaitlistEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Art. 20 — Data export
# ---------------------------------------------------------------------------

def _iso(value: Any) -> Any:
    """Recursively convert datetimes/UUIDs/Decimals into JSON-safe values."""
    import datetime as _dt
    import decimal as _dec

    if value is None:
        return None
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, _dec.Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _iso(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_iso(v) for v in value]
    return value


def _user_dict(user: User) -> dict[str, Any]:
    return _iso(
        {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "company_name": user.company_name,
            "subscription_plan": user.subscription_plan,
            "stripe_customer_id": user.stripe_customer_id,
            "stripe_subscription_id": user.stripe_subscription_id,
            "marketing_email_opt_in": user.marketing_email_opt_in,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }
    )


def _audit_dict(e: AuditLogEntry) -> dict[str, Any]:
    return _iso(
        {
            "id": e.id,
            "event_type": e.event_type,
            "meta": e.meta,
            "ip_address": str(e.ip_address) if e.ip_address is not None else None,
            "user_agent": e.user_agent,
            "created_at": e.created_at,
        }
    )


def _session_dict(s: UserSession) -> dict[str, Any]:
    return _iso(
        {
            "id": s.id,
            "user_agent": s.user_agent,
            "ip_address": str(s.ip_address) if s.ip_address is not None else None,
            "created_at": s.created_at,
            "last_used_at": s.last_used_at,
            "expires_at": s.expires_at,
            "revoked_at": s.revoked_at,
        }
    )


def _opening_dict(o: Opening) -> dict[str, Any]:
    return _iso(
        {
            "id": o.id,
            "opening_type": o.opening_type,
            "width_m": o.width_m,
            "height_m": o.height_m,
            "count": o.count,
            "description": o.description,
            "source": o.source,
            "area_m2": o.area_m2,
        }
    )


def _room_dict(r: Room) -> dict[str, Any]:
    return _iso(
        {
            "id": r.id,
            "plan_id": r.plan_id,
            "name": r.name,
            "room_number": r.room_number,
            "room_type": r.room_type,
            "area_m2": r.area_m2,
            "perimeter_m": r.perimeter_m,
            "height_m": r.height_m,
            "floor_type": r.floor_type,
            "wall_type": r.wall_type,
            "ceiling_type": r.ceiling_type,
            "is_wet_room": r.is_wet_room,
            "has_dachschraege": r.has_dachschraege,
            "is_staircase": r.is_staircase,
            "source": r.source,
            "ai_confidence": r.ai_confidence,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "openings": [_opening_dict(o) for o in r.openings],
        }
    )


def _unit_dict(u: Unit) -> dict[str, Any]:
    return _iso(
        {
            "id": u.id,
            "name": u.name,
            "unit_type": u.unit_type,
            "sort_order": u.sort_order,
            "rooms": [_room_dict(r) for r in u.rooms],
        }
    )


def _floor_dict(f: Floor) -> dict[str, Any]:
    return _iso(
        {
            "id": f.id,
            "name": f.name,
            "level_number": f.level_number,
            "floor_height_m": f.floor_height_m,
            "sort_order": f.sort_order,
            "units": [_unit_dict(u) for u in f.units],
        }
    )


def _building_dict(b: Building) -> dict[str, Any]:
    return _iso(
        {
            "id": b.id,
            "name": b.name,
            "sort_order": b.sort_order,
            "created_at": b.created_at,
            "floors": [_floor_dict(f) for f in b.floors],
        }
    )


def _plan_dict(p: Plan) -> dict[str, Any]:
    # file_path is the on-disk location; we expose the filename + size but
    # not the internal path, since it would leak the server layout.
    return _iso(
        {
            "id": p.id,
            "filename": p.filename,
            "file_size_bytes": p.file_size_bytes,
            "page_count": p.page_count,
            "plan_type": p.plan_type,
            "analysis_status": p.analysis_status,
            "created_at": p.created_at,
            # Pfad der Datei im Export-ZIP, oder None wenn die Datei auf
            # dem Server nicht (mehr) vorhanden ist.
            "file_in_export": _plan_export_name(p) if _readable_upload(p.file_path) else None,
        }
    )


# --- Export-Ergänzungen (Audit L10: consent_snapshots, Warteliste,
# API-Schlüssel, LV-Vorlagen, MCP-Protokoll, Nutzungsstatistik,
# Dateien). Keine Geheimnisse: Schlüssel-/Token-Hashes bleiben drin.


def _consent_dict(c: ConsentSnapshot) -> dict[str, Any]:
    return _iso(
        {
            "id": c.id,
            "event_type": c.event_type,
            "privacy_version": c.privacy_version,
            "terms_version": c.terms_version,
            "business_terms_version": c.business_terms_version,
            "marketing_optin": c.marketing_optin,
            "analytics_consent": c.analytics_consent,
            "ip_address": str(c.ip_address) if c.ip_address is not None else None,
            "user_agent": c.user_agent,
            "created_at": c.created_at,
        }
    )


def _waitlist_dict(w: WaitlistEntry) -> dict[str, Any]:
    # confirm_token_hash bewusst NICHT — ist ein Geheimnis, kein Inhalt.
    return _iso(
        {
            "id": w.id,
            "email": w.email,
            "name": w.name,
            "company_name": w.company_name,
            "status": w.status,
            "source": w.source,
            "consent_text_version": w.consent_text_version,
            "signup_at": w.signup_at,
            "signup_ip": w.signup_ip,
            "confirmed_at": w.confirmed_at,
            "confirmed_ip": w.confirmed_ip,
            "unsubscribed_at": w.unsubscribed_at,
        }
    )


def _api_key_dict(k: ApiKey) -> dict[str, Any]:
    # Nur Metadaten — key_hash bleibt auf dem Server.
    return _iso(
        {
            "id": k.id,
            "name": k.name,
            "key_prefix": k.key_prefix,
            "created_at": k.created_at,
            "last_used_at": k.last_used_at,
            "expires_at": k.expires_at,
            "revoked_at": k.revoked_at,
        }
    )


def _template_dict(t: LVTemplate) -> dict[str, Any]:
    return _iso(
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "category": t.category,
            "gewerk": t.gewerk,
            "template_data": t.template_data,
            "created_at": t.created_at,
            "updated_at": t.updated_at,
        }
    )


def _mcp_audit_dict(e: McpAuditLogEntry) -> dict[str, Any]:
    return _iso(
        {
            "id": e.id,
            "api_key_id": e.api_key_id,
            "tool_name": e.tool_name,
            "arguments": e.arguments,
            "result": e.result,
            "error_message": e.error_message,
            "latency_ms": e.latency_ms,
            "created_at": e.created_at,
        }
    )


def _analytics_dict(a: UsageAnalyticsEvent) -> dict[str, Any]:
    return _iso(
        {
            "event_type": a.event_type,
            "event_data": a.event_data,
            "region_code": a.region_code,
            "industry_segment": a.industry_segment,
            "created_at": a.created_at,
        }
    )


def _readable_upload(path_str: str | None) -> bool:
    """Datei existiert UND liegt im Upload-Verzeichnis (kein Pfad aus der
    DB darf den Export dazu bringen, fremde Server-Dateien einzupacken)."""
    from pathlib import Path

    if not path_str:
        return False
    try:
        path = Path(path_str).resolve()
        root = settings.upload_path.resolve()
    except OSError:
        return False
    return path.is_file() and (path == root or root in path.parents)


def _safe_name(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name)
    return keep.strip() or "datei"


def _plan_export_name(p: Plan) -> str:
    return f"dateien/plaene/{p.id}_{_safe_name(p.filename or 'plan.pdf')}"


def _logo_export_name(path_str: str) -> str:
    from pathlib import Path

    return f"dateien/logo{Path(path_str).suffix.lower()}"


def _berechnung_dict(b: Berechnungsnachweis) -> dict[str, Any]:
    # Column names in the DB still carry the historic ``onorm_`` prefix
    # (see backend/app/db/models/calculation.py). We rename them on the
    # way out so the exported JSON uses generic, norm-neutral keys.
    return _iso(
        {
            "id": b.id,
            "room_id": b.room_id,
            "raw_quantity": b.raw_quantity,
            "formula_description": b.formula_description,
            "formula_expression": b.formula_expression,
            "rule_factor": b.onorm_factor,
            "rule_ref": b.onorm_rule_ref,
            "rule_paragraph": b.onorm_paragraph,
            "deductions": b.deductions,
            "net_quantity": b.net_quantity,
            "unit": b.unit,
            "notes": b.notes,
            "created_at": b.created_at,
        }
    )


def _position_dict(p: Position) -> dict[str, Any]:
    return _iso(
        {
            "id": p.id,
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
            "berechnungsnachweise": [_berechnung_dict(b) for b in p.berechnungsnachweise],
        }
    )


def _gruppe_dict(g: Leistungsgruppe) -> dict[str, Any]:
    return _iso(
        {
            "id": g.id,
            "nummer": g.nummer,
            "bezeichnung": g.bezeichnung,
            "sort_order": g.sort_order,
            "positionen": [_position_dict(p) for p in g.positionen],
        }
    )


def _lv_dict(lv: Leistungsverzeichnis) -> dict[str, Any]:
    return _iso(
        {
            "id": lv.id,
            "name": lv.name,
            "trade": lv.trade,
            "status": lv.status,
            "vorbemerkungen": lv.vorbemerkungen,
            "created_at": lv.created_at,
            "updated_at": lv.updated_at,
            "gruppen": [_gruppe_dict(g) for g in lv.gruppen],
        }
    )


def _chat_session_dict(cs: ChatSession) -> dict[str, Any]:
    return _iso(
        {
            "id": cs.id,
            "project_id": cs.project_id,
            "title": cs.title,
            "created_at": cs.created_at,
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "content": m.content,
                    "context_refs": m.context_refs,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in cs.messages
            ],
        }
    )


def _project_dict(p: Project) -> dict[str, Any]:
    return _iso(
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "address": p.address,
            "client_name": p.client_name,
            "project_number": p.project_number,
            "grundstuecksnr": p.grundstuecksnr,
            "planverfasser": p.planverfasser,
            "status": p.status,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "plans": [_plan_dict(pl) for pl in p.plans],
            "buildings": [_building_dict(b) for b in p.buildings],
            "leistungsverzeichnisse": [_lv_dict(lv) for lv in p.leistungsverzeichnisse],
            "chat_sessions": [_chat_session_dict(cs) for cs in p.chat_sessions],
        }
    )


async def export_user_data(user: User, db: AsyncSession) -> dict[str, Any]:
    """Build a complete JSON-serializable dump of everything the user owns.

    The top-level shape is stable; we version it so downstream importers
    can migrate. When extending it (e.g. new related tables), bump the
    ``schema_version``.
    """
    from datetime import datetime, timezone

    # Eager-load the full object graph in a single round trip. The loader
    # tree mirrors the nesting in ``_project_dict``.
    stmt = (
        select(Project)
        .where(Project.user_id == user.id)
        .options(
            selectinload(Project.plans),
            selectinload(Project.buildings)
            .selectinload(Building.floors)
            .selectinload(Floor.units)
            .selectinload(Unit.rooms)
            .selectinload(Room.openings),
            selectinload(Project.leistungsverzeichnisse)
            .selectinload(Leistungsverzeichnis.gruppen)
            .selectinload(Leistungsgruppe.positionen)
            .selectinload(Position.berechnungsnachweise),
            selectinload(Project.chat_sessions).selectinload(ChatSession.messages),
        )
    )
    result = await db.execute(stmt)
    projects = result.scalars().unique().all()

    # Audit log and session history are also personal data — include them
    # so the export is a complete Art. 20 snapshot.
    audit_result = await db.execute(
        select(AuditLogEntry)
        .where(AuditLogEntry.user_id == user.id)
        .order_by(AuditLogEntry.created_at.asc())
    )
    audit_entries = audit_result.scalars().all()

    session_result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .order_by(UserSession.created_at.asc())
    )
    sessions = session_result.scalars().all()

    # Audit L10 — bisher fehlende Tabellen.
    from app.services.analytics import hash_user_id

    consents = (
        await db.execute(
            select(ConsentSnapshot)
            .where(ConsentSnapshot.user_id == user.id)
            .order_by(ConsentSnapshot.created_at.asc())
        )
    ).scalars().all()
    waitlist = (
        await db.execute(
            select(WaitlistEntry).where(
                WaitlistEntry.email == (user.email or "").strip().lower()
            )
        )
    ).scalars().all()
    api_keys = (
        await db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user.id)
            .order_by(ApiKey.created_at.asc())
        )
    ).scalars().all()
    templates = (
        await db.execute(
            select(LVTemplate)
            .where(
                LVTemplate.created_by_user_id == user.id,
                LVTemplate.is_system.is_(False),
            )
            .order_by(LVTemplate.created_at.asc())
        )
    ).scalars().all()
    mcp_entries = (
        await db.execute(
            select(McpAuditLogEntry)
            .where(McpAuditLogEntry.user_id == user.id)
            .order_by(McpAuditLogEntry.created_at.asc())
        )
    ).scalars().all()
    analytics = (
        await db.execute(
            select(UsageAnalyticsEvent)
            .where(UsageAnalyticsEvent.anonymous_user_id == hash_user_id(user.id))
            .order_by(UsageAnalyticsEvent.created_at.asc())
        )
    ).scalars().all()

    return {
        "schema_version": 3,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Dieser Export enthält alle personenbezogenen Daten, die BauLV "
            "zu diesem Konto speichert (Art. 15 und 20 DSGVO): Konto, "
            "Projekte mit Plänen, Räumen, Leistungsverzeichnissen und "
            "Chats, Sicherheitsprotokoll, Sitzungen, "
            "Einwilligungsnachweise, Warteliste-Eintrag, API-Schlüssel "
            "(nur Metadaten), eigene LV-Vorlagen, MCP-Aufrufprotokoll und "
            "pseudonymisierte Nutzungsstatistik. Hochgeladene Plan-Dateien "
            "und das Firmenlogo liegen im Ordner 'dateien/' dieses "
            "ZIP-Archivs, soweit sie auf dem Server noch vorhanden sind "
            "(siehe 'file_in_export' je Plan). Nicht enthalten: "
            "Geheimnisse wie Passwort-, Schlüssel- und Token-Hashes sowie "
            "allgemeine Referenzdaten ohne Personenbezug."
        ),
        "user": _user_dict(user),
        "projects": [_project_dict(p) for p in projects],
        "audit_log": [_audit_dict(e) for e in audit_entries],
        "sessions": [_session_dict(s) for s in sessions],
        "consent_snapshots": [_consent_dict(c) for c in consents],
        "waitlist_entries": [_waitlist_dict(w) for w in waitlist],
        "api_keys": [_api_key_dict(k) for k in api_keys],
        "lv_templates": [_template_dict(t) for t in templates],
        "mcp_audit_log": [_mcp_audit_dict(e) for e in mcp_entries],
        "usage_analytics": [_analytics_dict(a) for a in analytics],
        "logo_in_export": (
            _logo_export_name(user.logo_path)
            if _readable_upload(user.logo_path)
            else None
        ),
    }


async def build_export_zip(user: User, db: AsyncSession) -> tuple[bytes, int]:
    """Export als ZIP: ``daten.json`` + Plan-Dateien + Logo.

    Gibt ``(zip_bytes, anzahl_dateien)`` zurück. Dateien, die auf dem
    Server fehlen, stehen im JSON mit ``file_in_export: null``.
    """
    import io
    import json
    import zipfile

    payload = await export_user_data(user, db)
    plans = (
        await db.execute(
            select(Plan)
            .join(Project, Plan.project_id == Project.id)
            .where(Project.user_id == user.id)
        )
    ).scalars().all()

    buf = io.BytesIO()
    files = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "daten.json", json.dumps(payload, ensure_ascii=False, indent=2)
        )
        for p in plans:
            if _readable_upload(p.file_path):
                zf.write(p.file_path, _plan_export_name(p))
                files += 1
        if payload.get("logo_in_export"):
            zf.write(user.logo_path, payload["logo_in_export"])
            files += 1
    return buf.getvalue(), files


# ---------------------------------------------------------------------------
# Art. 17 — Account deletion
# ---------------------------------------------------------------------------

def _cancel_stripe_subscription(user: User) -> None:
    """Best-effort cancellation of the user's Stripe subscription.

    Failures are logged but must not block deletion: DSGVO Art. 17 gives
    the user an unconditional right to erasure on the grounds that they
    withdraw consent. If Stripe is offline, the worst case is a dangling
    subscription on Stripe's side that will eventually fail to charge a
    deleted customer — that's a billing problem, not a compliance one.
    """
    sub_id = user.stripe_subscription_id
    customer_id = user.stripe_customer_id
    if not sub_id and not customer_id:
        return

    try:
        import stripe

        from app.config import settings as _settings

        if not _settings.stripe_secret_key:
            return
        stripe.api_key = _settings.stripe_secret_key

        if sub_id:
            try:
                stripe.Subscription.delete(sub_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Stripe subscription cancel failed for user %s: %s",
                    user.id,
                    e,
                )

        if customer_id:
            try:
                stripe.Customer.delete(customer_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Stripe customer delete failed for user %s: %s",
                    user.id,
                    e,
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("Stripe teardown failed for user %s: %s", user.id, e)


def remove_project_upload_dir(project_id: UUID) -> bool:
    """Plan-Dateien eines Projekts entfernen (``upload_path/<project_id>/``).

    Gemeinsam genutzt von Konto-Löschung und Projekt-Löschung.
    Best-effort: eine festsitzende Datei blockiert die Löschung nicht,
    wir loggen und machen weiter. Gibt zurück, ob danach nichts mehr
    auf der Platte liegt.
    """
    pdir = settings.upload_path / str(project_id)
    if not pdir.exists():
        return True
    try:
        shutil.rmtree(pdir)
        return True
    except OSError as e:
        # A stuck file shouldn't block deletion. Log and continue;
        # operations can clean up orphans manually.
        logger.warning("Failed to remove plan directory %s: %s", pdir, e)
        return False


async def _delete_user_plan_files(user: User, db: AsyncSession) -> None:
    """Remove uploaded plan PDFs AND the user's logo dir from local storage.

    Two on-disk locations carry per-user content:

    * ``upload_path/<project_id>/`` — plan PDFs, one dir per project
    * ``upload_path/logos/<user_id>/`` — uploaded logo files
      (v24.3, siehe ``auth.py:_logo_dir_for``)

    Beide werden best-effort entfernt; eine festsitzende Datei darf
    die Account-Löschung nicht blockieren — wir loggen und machen weiter.
    Anything that doesn't exist is silently ignored — this is an
    idempotent cleanup, not an integrity check.
    """
    result = await db.execute(select(Project.id).where(Project.user_id == user.id))
    project_ids = [row[0] for row in result.all()]

    upload_root = settings.upload_path
    for pid in project_ids:
        remove_project_upload_dir(pid)

    # v24.4.9 — Logo-Verzeichnis mitlöschen. Pfad-Layout siehe
    # ``auth.py:_logo_dir_for``: ``upload_path/logos/<user_id>/``.
    # Pre-v24.4.9 blieb der Logo-Ordner als verwaister File-System-
    # Eintrag nach Account-Löschung stehen — ein gehostetes Firmenlogo
    # (B2B-Geschäftsdatum, bei Einzelunternehmern potenziell
    # personenbezogen) trotz DSGVO-Art-17-Löschanspruch. Idempotent:
    # wenn der Ordner nicht existiert (User hat nie ein Logo
    # hochgeladen), passiert nichts.
    logo_dir = upload_root / "logos" / str(user.id)
    if logo_dir.exists():
        try:
            shutil.rmtree(logo_dir)
        except OSError as e:
            logger.warning(
                "Failed to remove logo directory %s: %s", logo_dir, e
            )


async def delete_user_account(user: User, db: AsyncSession) -> None:
    """Irreversibly delete a user and everything they own.

    Order matters:

    1. Cancel Stripe *before* dropping the DB row so we can still read
       ``stripe_subscription_id``.
    2. Remove plan files *before* the cascading DB delete so we still
       have project IDs to locate the on-disk directories.
    3. Explicitly delete the user's chat sessions. Chat sessions only
       link to projects (not directly to users) and the FK uses
       ``ON DELETE SET NULL``, so the ``projects`` cascade would leave
       orphan rows behind. We find them via the user's projects and
       drop them first.
    4. Delete the user row — ``projects.user_id`` has ON DELETE CASCADE,
       so the project tree (plans, buildings, floors, units, rooms,
       openings, LVs, gruppen, positions, berechnungsnachweise) is
       removed in one statement.
    """
    from sqlalchemy import delete as sql_delete

    _cancel_stripe_subscription(user)
    await _delete_user_plan_files(user, db)

    # Pull the project IDs before the cascade so we can clean up chat
    # sessions that would otherwise become orphans (SET NULL).
    result = await db.execute(
        select(Project.id).where(Project.user_id == user.id)
    )
    project_ids = [row[0] for row in result.all()]

    if project_ids:
        # chat_messages has ON DELETE CASCADE from chat_sessions, so
        # deleting the sessions takes the messages with them.
        await db.execute(
            sql_delete(ChatSession).where(ChatSession.project_id.in_(project_ids))
        )

    await db.delete(user)
    await db.flush()
