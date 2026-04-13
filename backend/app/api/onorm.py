import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.db.models.onorm import ONormDokument, ONormRegel
from app.db.models.user import User
from app.schemas.onorm import (
    ONormDokumentResponse, ONormRegelResponse,
    ONormSearchRequest, ONormChunkResponse,
)
from app.onorm_rag.ingest import ingest_onorm_pdf
from app.onorm_rag.retriever import search_onorm_chunks
from app.auth import get_current_user

router = APIRouter()


@router.post("/upload", response_model=ONormDokumentResponse, status_code=201)
async def upload_onorm(
    file: UploadFile = File(...),
    norm_nummer: str = Query(..., description="e.g., 'B 2230-1'"),
    titel: str = Query(None),
    trade: str = Query(None, description="e.g., 'malerarbeiten'"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload an OENORM PDF for processing."""
    upload_dir = settings.upload_path / "onorm"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    dokument = ONormDokument(
        norm_nummer=norm_nummer,
        titel=titel,
        trade=trade,
        file_path=str(file_path),
    )
    db.add(dokument)
    await db.flush()

    try:
        chunks_created = await ingest_onorm_pdf(dokument.id, db)
        return dokument
    except Exception as e:
        raise HTTPException(500, f"Ingestion failed: {str(e)}")


@router.get("/dokumente", response_model=list[ONormDokumentResponse])
async def list_dokumente(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
    """Delete an OENORM document and its chunks/rules."""
    dokument = await db.get(ONormDokument, dokument_id)
    if not dokument:
        raise HTTPException(404, "Dokument nicht gefunden")
    await db.delete(dokument)
    await db.flush()


@router.post("/search", response_model=list[ONormChunkResponse])
async def search_onorm(
    data: ONormSearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search OENORM knowledge base using RAG."""
    chunks = await search_onorm_chunks(
        query=data.query,
        db=db,
        norm_nummer=data.norm_nummer,
        top_k=data.top_k,
    )
    return chunks
