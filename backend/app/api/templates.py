"""LV template library API.

Four endpoints:

    GET    /api/templates           list (system + caller's own)
    GET    /api/templates/{id}      detail with full positions payload
    POST   /api/templates           save an existing LV as a user template
    DELETE /api/templates/{id}      delete a user template (is_system=FALSE only)

The companion ``POST /api/lv/from-template`` endpoint that spawns a new
LV from a template lives in ``app.api.lv`` so it shares the LV create
plumbing — see ``create_lv_from_template`` there.

Ownership model:

    * System templates (``is_system=TRUE``) are readable by everyone,
      deletable by nobody.
    * User templates are readable by their creator; other users never
      see them. Kept simple on purpose — a sharing story can be added
      later without migrating rows.

All errors surface as German 4xx messages; 5xx cases log a traceback
with a recognisable prefix (``templates.*``) so Railway can grep.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.db.models.lv import Leistungsverzeichnis, Leistungsgruppe, Position
from app.db.models.lv_template import LVTemplate
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.template import (
    LVFromTemplateRequest,  # noqa: F401 (re-exported for symmetry with /api/lv)
    TemplateCreateFromLV,
    TemplateDetail,
    TemplateSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _structure_counts(template_data: dict | None) -> tuple[int, int]:
    """Return (gruppen_count, positionen_count) from a template_data blob.

    Defensive against malformed payloads — a seeded row should never hit
    the except branch, but a corrupt user template can and we'd rather
    render (0, 0) than 500 the whole list.
    """

    if not isinstance(template_data, dict):
        return 0, 0
    try:
        gruppen = template_data.get("gruppen") or []
        gruppen_count = len(gruppen)
        positionen_count = sum(
            len(g.get("positionen") or []) for g in gruppen if isinstance(g, dict)
        )
        return gruppen_count, positionen_count
    except Exception:  # pragma: no cover — extremely defensive
        logger.warning("templates._structure_counts malformed payload")
        return 0, 0


def _summary(row: LVTemplate) -> TemplateSummary:
    g, p = _structure_counts(row.template_data)
    return TemplateSummary(
        id=row.id,
        name=row.name,
        description=row.description,
        category=row.category,
        gewerk=row.gewerk,
        is_system=row.is_system,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        gruppen_count=g,
        positionen_count=p,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TemplateSummary])
async def list_templates(
    category: str | None = Query(default=None),
    gewerk: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List system templates + templates owned by the current user.

    Filters are optional; both match exactly. The response carries just
    the summary fields (name, counts, flags) — the positions payload is
    only returned by GET /api/templates/{id} so the library page doesn't
    ship hundreds of Langtext paragraphs on every load.
    """
    try:
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
        # System templates first, then user templates newest first.
        stmt = stmt.order_by(
            LVTemplate.is_system.desc(),
            LVTemplate.created_at.desc(),
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return [_summary(r) for r in rows]
    except Exception:
        logger.exception("templates.list_failed user_id=%s", user.id)
        raise HTTPException(
            500,
            "Vorlagen konnten nicht geladen werden. Der Fehler wurde "
            "protokolliert.",
        )


@router.get("/{template_id}", response_model=TemplateDetail)
async def get_template(
    template_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single template including the full positions payload.

    Enforces ownership: system templates are public, user templates are
    only visible to their creator.
    """
    tpl = await db.get(LVTemplate, template_id)
    if not tpl:
        raise HTTPException(404, "Vorlage nicht gefunden")
    if not tpl.is_system and tpl.created_by_user_id != user.id:
        # Treat as 404 to avoid leaking template existence across tenants.
        raise HTTPException(404, "Vorlage nicht gefunden")
    return TemplateDetail(
        id=tpl.id,
        name=tpl.name,
        description=tpl.description,
        category=tpl.category,
        gewerk=tpl.gewerk,
        is_system=tpl.is_system,
        created_by_user_id=tpl.created_by_user_id,
        created_at=tpl.created_at,
        updated_at=tpl.updated_at,
        template_data=tpl.template_data or {"gruppen": []},
    )


@router.post("", response_model=TemplateSummary, status_code=201)
async def create_template_from_lv(
    data: TemplateCreateFromLV,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save an existing LV as a user template.

    The LV's gruppen/positionen are copied into ``template_data`` with
    menge + einheitspreis stripped (templates are price- and
    quantity-agnostic — those belong to the concrete project). The new
    row is always ``is_system=False`` and owned by the caller.
    """
    # Ownership: the LV must belong to the caller's project. ``verify_lv_owner``
    # already does the 404/403 dance, reuse it instead of re-implementing.
    from app.api.ownership import verify_lv_owner

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
    if not lv:
        # Shouldn't hit — verify_lv_owner already checked — but be defensive.
        raise HTTPException(404, "LV nicht gefunden")

    # Build template_data by copying the LV structure. Prices and
    # quantities are intentionally dropped; they come from the project
    # the template is later applied to.
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
                    # No stored kategorie on Position — leave empty so
                    # the frontend renders it without a badge. Can be
                    # inferred heuristically later if needed.
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
            "Das LV ist leer — es gibt keine Positionen, die in eine Vorlage "
            "übernommen werden könnten.",
        )

    try:
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
        await db.flush()
        logger.info(
            "templates.created id=%s user_id=%s name=%r gruppen=%d positionen=%d",
            tpl.id,
            user.id,
            tpl.name,
            len(gruppen_payload),
            sum(len(g["positionen"]) for g in gruppen_payload),
        )
        return _summary(tpl)
    except Exception:
        logger.exception(
            "templates.create_failed user_id=%s lv_id=%s", user.id, data.lv_id
        )
        raise HTTPException(
            500,
            "Vorlage konnte nicht gespeichert werden. Der Fehler wurde "
            "protokolliert.",
        )


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user-owned template.

    System templates cannot be deleted regardless of caller — the API
    returns 403 explicitly instead of silently skipping the delete so
    the UI can show an intelligible message.
    """
    tpl = await db.get(LVTemplate, template_id)
    if not tpl:
        raise HTTPException(404, "Vorlage nicht gefunden")
    if tpl.is_system:
        raise HTTPException(
            403,
            "System-Vorlagen können nicht gelöscht werden.",
        )
    if tpl.created_by_user_id != user.id:
        # Same 404-masking as the GET — don't confirm existence to a
        # non-owner.
        raise HTTPException(404, "Vorlage nicht gefunden")

    try:
        await db.delete(tpl)
        await db.flush()
        logger.info(
            "templates.deleted id=%s user_id=%s", template_id, user.id
        )
    except Exception:
        logger.exception(
            "templates.delete_failed id=%s user_id=%s", template_id, user.id
        )
        raise HTTPException(
            500,
            "Vorlage konnte nicht gelöscht werden. Der Fehler wurde "
            "protokolliert.",
        )
