import asyncio
import io
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models.lv import Leistungsverzeichnis, Leistungsgruppe, Position
from app.db.models.project import Building, Floor, Room, Unit
from app.db.models.user import User
from app.schemas.lv import LVCreate, LVUpdate, LVResponse, PositionUpdate, PositionResponse
from app.schemas.template import LVFromTemplateRequest, LVFromTemplateResponse
from app.db.models.lv_template import LVTemplate
from app.calculation_engine.engine import calculate_lv
from app.lv_generator.generator import generate_position_texts
from app.export.xlsx_exporter import export_lv_xlsx
from app.export.pdf_exporter import export_lv_pdf
from app.auth import get_current_user
from app.api.ownership import verify_project_owner, verify_lv_owner
from app.subscriptions import require_feature, has_feature
from app.services import analytics as analytics_service
from app.db.models.analytics import (
    EVENT_LV_CREATED as ANALYTICS_EVENT_LV_CREATED,
    EVENT_POSITION_UPDATED as ANALYTICS_EVENT_POSITION_UPDATED,
    EVENT_TEMPLATE_USED as ANALYTICS_EVENT_TEMPLATE_USED,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/projects/{project_id}/lv", response_model=LVResponse, status_code=201,
             tags=["Leistungsverzeichnis"])
async def create_lv(
    project_id: UUID,
    data: LVCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_owner(project_id, user, db)
    lv = Leistungsverzeichnis(project_id=project_id, **data.model_dump())
    db.add(lv)
    await db.flush()

    # v23.8 — analytics signal. ``position_count`` is 0 at this
    # point (blank LV, no positions yet); the calculate / template
    # flow tracks the populated state separately.
    await analytics_service.record_event(
        db,
        event_type=ANALYTICS_EVENT_LV_CREATED,
        user=user,
        event_data={
            "trade": lv.trade.lower() if isinstance(lv.trade, str) else "",
            "position_count": 0,
        },
    )

    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv.id)
        .options(selectinload(Leistungsverzeichnis.gruppen))
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def _copy_template_payload_into_new_lv(
    tpl: LVTemplate,
    project_id: UUID,
    name_override: str | None,
    db: AsyncSession,
) -> tuple[Leistungsverzeichnis, int, int]:
    """Copy a template's gruppen/positionen payload into a fresh LV.

    The caller must have already authenticated the user and verified
    ownership of both the target project and the template. This helper
    is the pure copy mechanic — no auth, no audit logging — and is
    shared by the REST endpoint (``create_lv_from_template`` below)
    and the MCP ``create_lv_from_template`` tool. Keeping it in one
    place is the explicit "kein Drift zwischen REST und MCP"
    contract: any future tweak to the copy semantics flows to both
    surfaces simultaneously.

    Returns ``(lv, gruppen_created, positionen_created)``. The LV row
    is added to the session and flushed so its id is available;
    callers are responsible for the surrounding commit.

    Raises:
        HTTPException(400): the template's payload has no gruppen.
            The endpoint code translates that into the same
            user-facing message either way.
    """
    payload = tpl.template_data or {}
    gruppen_payload = payload.get("gruppen") or []
    if not gruppen_payload:
        # Defensive — a system template should never be empty, but a
        # corrupt user template could be. Refuse cleanly instead of
        # creating a blank LV.
        raise HTTPException(
            400,
            "Die ausgewählte Vorlage enthält keine Positionen.",
        )

    lv_name = (name_override or tpl.name).strip() or tpl.name
    lv = Leistungsverzeichnis(
        project_id=project_id,
        name=lv_name,
        trade=tpl.gewerk,
    )
    db.add(lv)
    await db.flush()  # need lv.id for FKs below

    gruppen_created = 0
    positionen_created = 0
    for g_idx, gruppe_src in enumerate(gruppen_payload):
        if not isinstance(gruppe_src, dict):
            continue
        gruppe = Leistungsgruppe(
            lv_id=lv.id,
            nummer=str(gruppe_src.get("nummer") or f"{g_idx + 1:02d}"),
            bezeichnung=str(gruppe_src.get("bezeichnung") or ""),
            sort_order=g_idx,
        )
        db.add(gruppe)
        await db.flush()
        gruppen_created += 1

        for p_idx, pos_src in enumerate(gruppe_src.get("positionen") or []):
            if not isinstance(pos_src, dict):
                continue
            kurztext = str(pos_src.get("kurztext") or "").strip()
            if not kurztext:
                continue  # skip malformed rows rather than 500
            pos = Position(
                gruppe_id=gruppe.id,
                positions_nummer=str(
                    pos_src.get("positions_nummer")
                    or f"{gruppe.nummer}.{p_idx + 1:02d}"
                ),
                kurztext=kurztext,
                langtext=pos_src.get("langtext"),
                einheit=str(pos_src.get("einheit") or "Stk"),
                # menge + einheitspreis stay NULL — Berechnen and
                # the user fill them in downstream.
                menge=None,
                einheitspreis=None,
                positionsart="normal",
                text_source="template",
                is_locked=False,
                sort_order=p_idx,
            )
            db.add(pos)
            positionen_created += 1
    await db.flush()
    return lv, gruppen_created, positionen_created


@router.post(
    "/from-template",
    response_model=LVFromTemplateResponse,
    status_code=201,
    tags=["Leistungsverzeichnis"],
)
async def create_lv_from_template(
    data: LVFromTemplateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Spawn a new Leistungsverzeichnis inside a project from a template.

    Copies the template's gruppen/positionen structure into fresh DB
    rows (``leistungsgruppen`` + ``positionen``) with no quantities and
    no prices — those flow in later from the project's rooms (``/calculate``)
    and from the user's manual input.

    Ownership is enforced on both sides:

    * The caller must own the target project.
    * The template must be either a system template or owned by the
      caller.

    Returns the new LV's id + counts so the frontend can navigate
    straight to it without a second round trip.
    """
    await verify_project_owner(data.project_id, user, db)

    tpl = await db.get(LVTemplate, data.template_id)
    if not tpl:
        raise HTTPException(404, "Vorlage nicht gefunden")
    if not tpl.is_system and tpl.created_by_user_id != user.id:
        # 404-mask across tenants so existence isn't leaked.
        raise HTTPException(404, "Vorlage nicht gefunden")

    try:
        lv, gruppen_created, positionen_created = (
            await _copy_template_payload_into_new_lv(
                tpl, data.project_id, data.name, db
            )
        )

        logger.info(
            "lv.from_template lv_id=%s project_id=%s template_id=%s "
            "user_id=%s gruppen=%d positionen=%d",
            lv.id,
            data.project_id,
            data.template_id,
            user.id,
            gruppen_created,
            positionen_created,
        )

        # v23.8 — analytics signal. ``template_id`` is recorded only
        # for system templates (``is_system=True``) — user-owned
        # template ids are private (they're a per-user identifier
        # space) and would risk re-identification in aggregate
        # data. For user templates we still emit the event so the
        # raw count is right, but we send a generic placeholder id.
        await analytics_service.record_event(
            db,
            event_type=ANALYTICS_EVENT_TEMPLATE_USED,
            user=user,
            event_data={
                "template_id": (
                    str(tpl.id) if tpl.is_system else "user_template"
                ),
                "is_system": tpl.is_system,
            },
        )

        return LVFromTemplateResponse(
            lv_id=lv.id,
            project_id=lv.project_id,
            name=lv.name,
            trade=lv.trade,
            gruppen_created=gruppen_created,
            positionen_created=positionen_created,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "lv.from_template_failed project_id=%s template_id=%s user_id=%s",
            data.project_id,
            data.template_id,
            user.id,
        )
        raise HTTPException(
            500,
            "LV konnte nicht aus der Vorlage erstellt werden. Der Fehler "
            "wurde protokolliert.",
        )


@router.get("/projects/{project_id}/lv", response_model=list[LVResponse],
            tags=["Leistungsverzeichnis"])
async def list_lvs(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List every LV belonging to this project.

    Past bug (fixed 2026-04-23, v15): this endpoint returned 500 on any
    project that had ever run the wall-calc / LV-calc pipelines. Root
    cause was not a dangling FK — SQLAlchemy's CASCADE on
    ``berechnungsnachweise.room_id`` correctly clears orphans when a
    room is cascade-deleted — but a missing eager-load. ``LVResponse``
    recursively serialises ``gruppen → positionen → berechnungsnachweise``.
    The old implementation only eager-loaded ``gruppen``, so Pydantic's
    serialiser would trigger lazy loads on ``positionen`` and
    ``berechnungsnachweise`` from within the async request context,
    which raises ``MissingGreenlet`` → 500. Fresh projects had no
    positionen so the issue never triggered; the ``Wandflächen Test``
    project did and every GET to this endpoint failed after the first
    ``/calculate`` run.

    Fix: eager-load the full tree here so the serialiser never touches
    an unloaded attribute. Any future bug is caught by the explicit
    try/except that logs the traceback to Railway before we raise 500
    — no more silent exceptions disguised as gateway errors.
    """
    await verify_project_owner(project_id, user, db)
    try:
        result = await db.execute(
            select(Leistungsverzeichnis)
            .where(Leistungsverzeichnis.project_id == project_id)
            .options(
                selectinload(Leistungsverzeichnis.gruppen)
                .selectinload(Leistungsgruppe.positionen)
                .selectinload(Position.berechnungsnachweise),
            )
            .order_by(Leistungsverzeichnis.created_at.desc())
        )
        return result.scalars().all()
    except Exception:
        logger.exception(
            "list_lvs.failed project_id=%s user_id=%s", project_id, user.id
        )
        raise HTTPException(
            500,
            "Leistungsverzeichnisse konnten nicht geladen werden. Der "
            "Fehler wurde protokolliert — bitte kontaktieren Sie den "
            "Support, falls das Problem bestehen bleibt.",
        )


@router.get("/{lv_id}", response_model=LVResponse)
async def get_lv(
    lv_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_lv_owner(lv_id, user, db)
    try:
        stmt = (
            select(Leistungsverzeichnis)
            .where(Leistungsverzeichnis.id == lv_id)
            .options(
                selectinload(Leistungsverzeichnis.gruppen)
                .selectinload(Leistungsgruppe.positionen)
                .selectinload(Position.berechnungsnachweise),
            )
        )
        result = await db.execute(stmt)
        lv = result.scalars().first()
        if not lv:
            raise HTTPException(404, "LV nicht gefunden")
        return lv
    except HTTPException:
        raise
    except Exception:
        logger.exception("get_lv.failed lv_id=%s user_id=%s", lv_id, user.id)
        raise HTTPException(
            500,
            "Leistungsverzeichnis konnte nicht geladen werden. Der "
            "Fehler wurde protokolliert.",
        )


@router.put("/{lv_id}", response_model=LVResponse)
async def update_lv(
    lv_id: UUID,
    data: LVUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lv = await verify_lv_owner(lv_id, user, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(lv, key, value)
    await db.flush()
    return lv


@router.post("/{lv_id}/calculate")
async def run_calculation(
    lv_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the deterministic calculation engine for this LV.

    Pre-flight: returns a 400 with a friendly German message if the
    project has zero rooms. The calculation engine itself raises
    ``ValueError`` in that case (see ``calculate_lv``); the early
    check here gives operators a clean grep target ("calculate.no_rooms")
    and guards against any future engine refactor that might let the
    no-rooms path fall through to a 500. v23.7 hardened this after a
    tester observed the engine raising for a hypothetical edge case
    where ``rooms`` was empty but the trade calculator's internal
    invariants asserted on a non-empty list.
    """
    await verify_lv_owner(lv_id, user, db)

    # Pre-flight room count. The engine ALSO checks this and raises a
    # ValueError → 400, but doing it here means the failure mode is
    # explicit at the API surface and we never even spin up the
    # eager-load query for an LV we know can't be calculated.
    lv = await db.get(Leistungsverzeichnis, lv_id)
    if lv is None:
        # Defence in depth — verify_lv_owner above already 404s if
        # ownership/lookup fails, but the explicit branch makes the
        # intent unambiguous.
        raise HTTPException(404, "LV nicht gefunden")
    room_count_stmt = (
        select(func.count(Room.id))
        .join(Unit, Room.unit_id == Unit.id)
        .join(Floor, Unit.floor_id == Floor.id)
        .join(Building, Floor.building_id == Building.id)
        .where(Building.project_id == lv.project_id)
    )
    room_count = (await db.execute(room_count_stmt)).scalar_one()
    if room_count == 0:
        logger.info(
            "calculate.no_rooms lv_id=%s project_id=%s user_id=%s",
            lv_id,
            lv.project_id,
            user.id,
        )
        raise HTTPException(
            400,
            "Bitte zuerst Räume hinzufügen oder einen Bauplan analysieren, "
            "bevor Sie berechnen. Sie können entweder einen Plan hochladen "
            "und über die Plananalyse Räume extrahieren oder die "
            "Gebäudestruktur manuell anlegen.",
        )

    try:
        results = await calculate_lv(lv_id, db)
        return {
            "lv_id": str(lv_id),
            "positions_calculated": len(results),
            "total_measurement_lines": sum(len(r.measurement_lines) for r in results),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{lv_id}/generate-texts")
async def generate_texts(
    lv_id: UUID,
    user: User = Depends(require_feature("ai_position_generator")),
    db: AsyncSession = Depends(get_db),
):
    """Generate AI-powered position texts — requires Pro plan."""
    await verify_lv_owner(lv_id, user, db)

    # Pre-flight: AI text generation on an empty LV used to surface as a
    # 500 (downstream Claude call on a zero-position payload, or silent
    # no-op that the frontend couldn't distinguish from success). Check
    # position count up front and return a 400 with an actionable German
    # message, mirroring the ``/calculate`` endpoint's 400-on-ValueError
    # contract. The user needs to either run Plananalyse (rooms →
    # ``/calculate`` → positions) or add positions manually before this
    # endpoint has anything to work with.
    count_stmt = (
        select(func.count(Position.id))
        .join(Leistungsgruppe, Position.gruppe_id == Leistungsgruppe.id)
        .where(Leistungsgruppe.lv_id == lv_id)
    )
    position_count = (await db.execute(count_stmt)).scalar_one()
    if position_count == 0:
        raise HTTPException(
            400,
            "Bitte zuerst Räume über die Plananalyse erfassen oder manuell "
            "Positionen hinzufügen, bevor Sie AI-Texte generieren.",
        )

    try:
        updated = await generate_position_texts(lv_id, db)
        return {"lv_id": str(lv_id), "positions_updated": updated}
    except ValueError as e:
        raise HTTPException(400, str(e))


# Keywords we use to identify a position as "wall-area based". The
# Wandberechnung service computes a single number (total net wall
# area across all rooms) and the sync endpoint fans it out into every
# position whose kurztext/langtext mentions any of these German
# construction terms. The list intentionally covers the four trades
# the product spec names: Malerarbeiten (anstrich/malerei), Tapezier-
# arbeiten (tapete/tapezier), Fliesenlegearbeiten (fliesen/wandfliese),
# Verputzarbeiten (putz/verputz). "wand" on its own catches generic
# "Wandbeschichtung" / "Wandverkleidung" positions the AI text
# generator may emit.
_WALL_POSITION_KEYWORDS: tuple[str, ...] = (
    "wand",           # wandfläche, wandanstrich, wandbeschichtung, …
    "tapet",          # tapete, tapezierarbeiten, tapezieren
    "anstrich",       # anstrich (wall painting)
    "malerei",        # malerei, malerarbeiten
    "fliese",         # fliesen, wandfliesen, fliesenarbeiten
    "verputz",        # verputz, verputzarbeiten
    "putz",           # putz (matches innenputz, glattputz, …)
)

# Ceiling keywords. Positions for "Deckenanstrich" / "Deckenmalerei" /
# "Decken-" etc. used to get overwritten with wall area when a user
# clicked Wandflächen, because "anstrich" matched "Deckenanstrich" and
# no ceiling-side check excluded it. Ceilings are measured in floor
# (slab) area, not wall area. We detect ceiling intent with this list,
# route those positions to the sum of ``room.area_m2`` (the net
# floor/slab area — which equals ceiling area for any sane room), and
# keep wall routing for everything else that still matches
# ``_WALL_POSITION_KEYWORDS``.
_CEILING_POSITION_KEYWORDS: tuple[str, ...] = (
    "decke",          # decken, deckenanstrich, deckenmalerei, decken-
)


def _position_haystack(position: "Position") -> str:
    """Normalized kurztext+langtext for keyword matching."""
    return " ".join(
        (position.kurztext or "", position.langtext or "")
    ).lower()


def _is_m2_position(position: "Position") -> bool:
    """True iff this position is measured in m² (or synonyms)."""
    einheit = (position.einheit or "").lower().strip()
    # Accept "m2", "m²", "qm"; reject anything else.
    return einheit in {"m2", "m²", "qm"}


def _is_ceiling_position(position: "Position") -> bool:
    """True iff this position's text looks like ceiling work.

    Ceilings are checked BEFORE walls because the string "anstrich"
    legitimately appears in both "Wandanstrich" and "Deckenanstrich"
    — letting wall match first would misroute ceiling positions.
    """
    if not _is_m2_position(position):
        return False
    return any(kw in _position_haystack(position) for kw in _CEILING_POSITION_KEYWORDS)


def _is_wall_position(position: "Position") -> bool:
    """Return True iff this position's text looks like wall-area work.

    We check ``kurztext`` and ``langtext`` (both lowercased) against
    the keyword list. Also require ``einheit`` to be ``m2`` / ``m²``
    so we don't accidentally overwrite linear-metre ("lfm") or
    per-piece positions that happen to mention "Wand" in their text.

    Explicitly excludes ceiling positions (see ``_is_ceiling_position``)
    so "Deckenanstrich" doesn't get matched by the "anstrich" keyword.
    """
    if not _is_m2_position(position):
        return False
    if _is_ceiling_position(position):
        return False
    return any(kw in _position_haystack(position) for kw in _WALL_POSITION_KEYWORDS)


@router.post("/{lv_id}/sync-wall-areas")
async def sync_wall_areas(
    lv_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Übertrage Netto-Wand- und Deckenflächen in passende LV-Positionen.

    * Wand-Positionen (m², Schlagworte Wand/Tapete/Anstrich/Fliesen/
      Putz — aber **nicht** Decke) erhalten die Summe der Netto-
      Wandflächen aller Räume.
    * Decken-Positionen (m², Schlagwort "Decke", deckt
      Deckenanstrich / Deckenmalerei / Decken- ab) erhalten die
      Summe der Grundflächen (``area_m2``) aller Räume. Früher
      wurde "Deckenanstrich" fälschlich als Wand-Position erkannt,
      weil "anstrich" matcht — ab v15 werden Decken-Positionen vor
      dem Wand-Match aussortiert.
    * Gesperrte Positionen (``is_locked``) werden übersprungen.

    Antwort:

    ```
    {
      "lv_id": ...,
      "total_wall_area_m2": 1234.56,
      "total_ceiling_area_m2": 678.90,
      "wall_positions_updated": 3,
      "ceiling_positions_updated": 1,
      "positions_updated": 4,              # total = wall + ceiling
      "positions_skipped_locked": 1,
      "rooms_considered": 42
    }
    ```

    v16 hardening: the whole body is wrapped in a try/except that
    logs ``sync_wall_areas.failed`` with a full traceback. v15 had no
    outer handler, so the AttributeError below (``r.floor_area_m2``
    — wrong column name; correct is ``r.area_m2``) surfaced as a
    generic 500 with nothing in Railway logs to grep for. The outer
    handler means any future breakage leaves a clear trace and a
    German error message the UI can show instead of "Serverfehler
    (500)".
    """
    try:
        lv = await verify_lv_owner(lv_id, user, db)

        # Load every room in the LV's project with its precomputed
        # wall-area cache. We don't re-run the calculator here — the
        # Wandberechnung page is where the user is expected to confirm
        # heights and trigger the bulk calc. This endpoint just reads
        # the cached value and fans it out.
        stmt = (
            select(Room)
            .join(Unit).join(Floor).join(Building)
            .where(Building.project_id == lv.project_id)
        )
        rooms = (await db.execute(stmt)).scalars().all()

        if not rooms:
            raise HTTPException(
                400,
                "Für dieses Projekt wurden noch keine Räume erfasst. Bitte "
                "zuerst die Plananalyse durchführen oder Räume manuell "
                "anlegen, bevor Wandflächen übernommen werden können.",
            )

        total_wall = sum(
            float(r.wall_area_net_m2) for r in rooms if r.wall_area_net_m2 is not None
        )
        # v15 used ``r.floor_area_m2`` here — that column doesn't
        # exist on Room (the slab area is named ``area_m2``, matching
        # everywhere else in the codebase: calculation_engine,
        # chat.assistant, dsgvo). Accessing the non-existent attribute
        # raised AttributeError → 500 → frontend never got the sync
        # result. v16 uses the correct column name.
        total_ceiling = sum(
            float(r.area_m2) for r in rooms if r.area_m2 is not None
        )
        # Round to the 2-decimal precision the UI and LV share.
        total_wall_rounded = round(total_wall, 2)
        total_ceiling_rounded = round(total_ceiling, 2)

        logger.info(
            "sync_wall_areas.totals lv_id=%s rooms=%d "
            "total_wall_m2=%s total_ceiling_m2=%s",
            lv_id, len(rooms), total_wall_rounded, total_ceiling_rounded,
        )

        # Load the LV's positions through groups.
        pos_stmt = (
            select(Position)
            .join(Leistungsgruppe, Position.gruppe_id == Leistungsgruppe.id)
            .where(Leistungsgruppe.lv_id == lv_id)
        )
        positions = list((await db.execute(pos_stmt)).scalars().all())

        wall_updated = 0
        ceiling_updated = 0
        skipped_locked = 0
        for pos in positions:
            # Ceiling check runs FIRST — "Deckenanstrich" matches both the
            # ceiling keyword and several wall keywords ("anstrich"), so
            # the order here is load-bearing. Don't reorder.
            if _is_ceiling_position(pos):
                if pos.is_locked:
                    skipped_locked += 1
                    logger.info(
                        "sync_wall_areas.skip_locked lv_id=%s position_id=%s "
                        "kind=ceiling kurztext=%r",
                        lv_id, pos.id, (pos.kurztext or "")[:60],
                    )
                    continue
                pos.menge = total_ceiling_rounded
                ceiling_updated += 1
                logger.info(
                    "sync_wall_areas.apply lv_id=%s position_id=%s "
                    "kind=ceiling menge=%s kurztext=%r",
                    lv_id, pos.id, total_ceiling_rounded, (pos.kurztext or "")[:60],
                )
                continue
            if _is_wall_position(pos):
                if pos.is_locked:
                    skipped_locked += 1
                    logger.info(
                        "sync_wall_areas.skip_locked lv_id=%s position_id=%s "
                        "kind=wall kurztext=%r",
                        lv_id, pos.id, (pos.kurztext or "")[:60],
                    )
                    continue
                pos.menge = total_wall_rounded
                wall_updated += 1
                logger.info(
                    "sync_wall_areas.apply lv_id=%s position_id=%s "
                    "kind=wall menge=%s kurztext=%r",
                    lv_id, pos.id, total_wall_rounded, (pos.kurztext or "")[:60],
                )
                continue
            # Neither kind — skip silently. Positions like "Stundenlohn"
            # or "Pauschale" legitimately don't match any keyword.

        await db.flush()

        logger.info(
            "sync_wall_areas.done lv_id=%s wall_updated=%d "
            "ceiling_updated=%d skipped_locked=%d",
            lv_id, wall_updated, ceiling_updated, skipped_locked,
        )

        return {
            "lv_id": str(lv_id),
            "total_wall_area_m2": total_wall_rounded,
            "total_ceiling_area_m2": total_ceiling_rounded,
            "wall_positions_updated": wall_updated,
            "ceiling_positions_updated": ceiling_updated,
            # Keep the legacy key so older frontend builds still read something
            # meaningful — the UI toast just wants a total count.
            "positions_updated": wall_updated + ceiling_updated,
            "positions_skipped_locked": skipped_locked,
            "rooms_considered": len(rooms),
        }
    except HTTPException:
        # HTTPException is not a bug — it's a deliberate 400/403/404
        # response. Let FastAPI serialise it as-is.
        raise
    except Exception:
        logger.exception(
            "sync_wall_areas.failed lv_id=%s user_id=%s", lv_id, user.id
        )
        raise HTTPException(
            500,
            "Wandflächen konnten nicht übernommen werden. Der Fehler "
            "wurde protokolliert — bitte kontaktieren Sie den Support, "
            "falls das Problem bestehen bleibt.",
        )


@router.put("/positionen/{position_id}", response_model=PositionResponse)
async def update_position(
    position_id: UUID,
    data: PositionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partially update a single position (v23.5 inline-edit surface).

    Lock semantics
    --------------

    A position with ``is_locked=True`` rejects every field *except*
    ``is_locked`` itself. That guarantees:

    * ``calculate_lv`` and ``sync_wall_areas`` can mass-update without
      blowing away a reviewer's manual override (those flows already
      check the lock — this endpoint enforces the same rule for the
      direct-edit path).
    * The user can always *unlock* via this endpoint, since toggling
      the lock flag itself is the one thing locked rows allow.

    The check rejects the *whole request* with a 409 rather than
    silently dropping the protected fields — surfacing a confusing
    "we kept some of your changes" state to the user is worse than
    a clear "the position is locked, unlock first" error.
    """
    stmt = (
        select(Position)
        .where(Position.id == position_id)
        .options(selectinload(Position.berechnungsnachweise))
    )
    result = await db.execute(stmt)
    position = result.scalars().first()
    if not position:
        raise HTTPException(404, "Position nicht gefunden")

    # Verify ownership: Position -> Gruppe -> LV -> Project
    gruppe = await db.get(Leistungsgruppe, position.gruppe_id)
    if not gruppe:
        raise HTTPException(404, "Leistungsgruppe nicht gefunden")
    await verify_lv_owner(gruppe.lv_id, user, db)

    incoming = data.model_dump(exclude_unset=True)

    # Lock gate. Allow the lock flag itself through (so the user can
    # unlock); reject every other field with 409. We check the
    # *current* lock state — if the request both unlocks AND edits in
    # the same payload, that's still rejected, because the row is
    # locked at the moment of the write. Two-step: unlock, then edit.
    if position.is_locked and (set(incoming.keys()) - {"is_locked"}):
        raise HTTPException(
            409,
            "Position ist gesperrt — bitte zuerst die Sperre aufheben, "
            "bevor Menge oder Einheitspreis geändert werden.",
        )

    for key, value in incoming.items():
        setattr(position, key, value)
    if data.kurztext or data.langtext:
        position.text_source = "manual"
    await db.flush()

    # v23.8 — analytics signal. We only emit on actual data edits
    # (Menge / Einheitspreis), not on lock-toggle alone — the lock
    # is metadata, not "user is doing real work". Bucket the price
    # so raw EUR values never land in analytics; the buckets are
    # pre-defined privacy-safe ranges.
    edit_keys = set(incoming.keys()) - {"is_locked"}
    if edit_keys:
        ep = position.einheitspreis
        ep_float = float(ep) if ep is not None else None
        await analytics_service.record_event(
            db,
            event_type=ANALYTICS_EVENT_POSITION_UPDATED,
            user=user,
            event_data={
                "has_price": ep_float is not None and ep_float > 0,
                "has_quantity": (
                    position.menge is not None
                    and float(position.menge) > 0
                ),
                "price_bucket": _price_bucket(ep_float),
            },
        )
    return position


# v23.8 — privacy-safe price buckets. Maps a raw EUR value to a
# coarse range string. The whitelist for ``position_updated``
# events accepts only these strings, so a contributor accidentally
# trying to log raw prices would hit the sanitiser's regex and
# the event would be rejected.
_PRICE_BUCKETS: tuple[tuple[float, str], ...] = (
    (5.0, "0-5_eur"),
    (15.0, "5-15_eur"),
    (50.0, "15-50_eur"),
    (150.0, "50-150_eur"),
    (500.0, "150-500_eur"),
    (1500.0, "500-1500_eur"),
    (float("inf"), "1500plus_eur"),
)


def _price_bucket(value: float | None) -> str:
    """Map a raw EUR value to a privacy-safe range bucket."""
    if value is None or value <= 0:
        return "none"
    for upper, label in _PRICE_BUCKETS:
        if value < upper:
            return label
    return "1500plus_eur"


# Upper bound on how long an export is allowed to run before we give
# up and return a retry-able 503. Railway's proxy itself cuts the
# request at ~60s with a bare 502 Bad Gateway — worse than useless for
# the client because there's no Retry-After and the client can't tell
# whether it was a transient cold-start or a real bug. We run the
# export under ``asyncio.wait_for`` with a shorter budget so WE control
# the failure shape: 503 + Retry-After tells the frontend "try again
# in N seconds" and the axios interceptor then handles the retry
# automatically without the user noticing.
_EXPORT_TIMEOUT_SECONDS = 45
_EXPORT_RETRY_AFTER_SECONDS = 3


@router.post("/{lv_id}/export")
async def export_lv(
    lv_id: UUID,
    format: str = "xlsx",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export LV as Excel (Pro-gated) or PDF (available to all authenticated users).

    The pricing page lists PDF as a Basis-plan feature; the feature matrix
    (``app/subscriptions.py::get_feature_matrix``) already returns
    ``pdf_export: True`` unconditionally. The old implementation gated the
    whole route with ``require_feature("excel_export")``, which bounced
    Basis users with a 403 before the format dispatch could hand them off
    to the PDF branch. We now check the per-format plan requirement inside
    the dispatch so the gate matches the advertised pricing matrix.

    Timeout handling: reportlab's first invocation on a cold Railway
    dyno imports ~30 modules and can take 15-40s. Railway's edge proxy
    doesn't wait that long and returns a raw 502 to the browser,
    making the export look broken. We wrap the PDF branch in
    ``asyncio.wait_for`` so we surface a well-formed 503 + Retry-After
    that the frontend can retry automatically.
    """
    await verify_lv_owner(lv_id, user, db)
    if format == "xlsx":
        # Excel remains Pro-only, matching ``FEATURE_REQUIREMENTS``.
        if not has_feature(user.subscription_plan, "excel_export"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Excel Export erfordert den Pro-Plan. "
                    "Bitte upgraden Sie Ihr Abonnement."
                ),
            )
        try:
            xlsx_bytes = await asyncio.wait_for(
                export_lv_xlsx(lv_id, db), timeout=_EXPORT_TIMEOUT_SECONDS
            )
            return StreamingResponse(
                io.BytesIO(xlsx_bytes),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": f"attachment; filename=LV_{lv_id}.xlsx"
                },
            )
        except ValueError as e:
            raise HTTPException(404, str(e))
        except asyncio.TimeoutError:
            logger.warning(
                "export_lv.timeout format=xlsx lv_id=%s user_id=%s", lv_id, user.id
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Excel-Export dauert zu lange. Bitte in wenigen "
                    "Sekunden erneut versuchen."
                ),
                headers={"Retry-After": str(_EXPORT_RETRY_AFTER_SECONDS)},
            )
        except HTTPException:
            # An export helper might raise HTTPException itself (e.g.
            # for a 403 on a protected resource). Let FastAPI pass it
            # through — otherwise the generic ``except Exception``
            # below would swallow it and return 500 with an opaque
            # "Excel-Export fehlgeschlagen." body.
            raise
        except Exception:
            logger.exception(
                "export_lv.failed format=xlsx lv_id=%s user_id=%s", lv_id, user.id
            )
            raise HTTPException(500, "Excel-Export fehlgeschlagen.")
    if format == "pdf":
        # PDF is a Basis-tier feature — no plan gate, just ownership.
        try:
            pdf_bytes = await asyncio.wait_for(
                export_lv_pdf(lv_id, db), timeout=_EXPORT_TIMEOUT_SECONDS
            )
            return StreamingResponse(
                io.BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"attachment; filename=LV_{lv_id}.pdf"
                },
            )
        except ValueError as e:
            raise HTTPException(404, str(e))
        except asyncio.TimeoutError:
            # reportlab cold-start on Railway: surface a retry-able 503
            # so the frontend's axios interceptor re-tries transparently
            # instead of the user seeing a raw 502 gateway error.
            logger.warning(
                "export_lv.timeout format=pdf lv_id=%s user_id=%s", lv_id, user.id
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "PDF-Export braucht gerade länger als gewöhnlich "
                    "(Kaltstart). Bitte in wenigen Sekunden erneut "
                    "versuchen."
                ),
                headers={"Retry-After": str(_EXPORT_RETRY_AFTER_SECONDS)},
            )
        except HTTPException:
            # Same reasoning as the xlsx branch — don't swallow
            # deliberate HTTPExceptions in the catch-all below.
            raise
        except Exception:
            logger.exception(
                "export_lv.failed format=pdf lv_id=%s user_id=%s", lv_id, user.id
            )
            raise HTTPException(500, "PDF-Export fehlgeschlagen.")
    raise HTTPException(
        400,
        f"Format '{format}' nicht unterstützt. Bitte 'xlsx' oder 'pdf' wählen.",
    )
