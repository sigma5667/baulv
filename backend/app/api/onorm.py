"""ÖNORM API — read-only after the copyright compliance refactor.

Historically this module allowed uploading ÖNORM PDFs and storing them in the
database for full-text RAG retrieval. That violated Austrian Standards
International's copyright on the ÖNORM text (the text itself is protected,
even though the underlying mathematical rules are not).

The upload and RAG-search endpoints have been removed. BauLV now ships the
calculation rules as plain Python in ``app/calculation_engine/trades/`` and
the on-disk ÖNORM PDF store has been retired. The remaining endpoints
(``GET /dokumente``, ``GET /regeln``, ``DELETE /dokumente/{id}``) are kept
so existing frontend callers don't 404 — they operate on whatever legacy
rows may still exist and can be removed entirely once the frontend stops
calling them.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.onorm import ONormDokument, ONormRegel
from app.db.models.user import User
from app.schemas.onorm import ONormDokumentResponse, ONormRegelResponse
from app.auth import get_current_user

router = APIRouter()


@router.get("/dokumente", response_model=list[ONormDokumentResponse])
async def list_dokumente(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List legacy ÖNORM document registry entries.

    Kept as a read-only endpoint for the transition period. No new entries
    can be created because ``POST /upload`` has been removed. In a green
    database this returns an empty list.
    """
    result = await db.execute(
        select(ONormDokument).order_by(ONormDokument.norm_nummer)
    )
    return result.scalars().all()


@router.get("/regeln", response_model=list[ONormRegelResponse])
async def list_regeln(
    trade: str = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List coded rule metadata (not covered by copyright — these are
    mathematical formulas and parameters, not ÖNORM text)."""
    stmt = select(ONormRegel)
    if trade:
        stmt = stmt.where(ONormRegel.trade == trade)
    stmt = stmt.order_by(ONormRegel.regel_code)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/dokumente/{dokument_id}", status_code=204)
async def delete_dokument(
    dokument_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a legacy ÖNORM document registry entry."""
    dokument = await db.get(ONormDokument, dokument_id)
    if not dokument:
        raise HTTPException(404, "Dokument nicht gefunden")
    await db.delete(dokument)
    await db.flush()
