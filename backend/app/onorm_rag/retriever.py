"""ÖNORM RAG retriever: search chunks by text similarity."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.onorm import ONormChunk, ONormDokument


async def search_onorm_chunks(
    query: str,
    db: AsyncSession,
    norm_nummer: str | None = None,
    dokument_ids: list[UUID] | None = None,
    top_k: int = 5,
) -> list[ONormChunk]:
    """Search ÖNORM chunks using full-text search.

    For MVP, uses PostgreSQL full-text search.
    Can be upgraded to vector similarity search with pgvector when embeddings are added.

    Args:
        dokument_ids: If provided, only search within these specific ÖNORM documents.
    """
    stmt = (
        select(ONormChunk)
        .where(ONormChunk.chunk_text.ilike(f"%{query}%"))
    )

    if dokument_ids:
        stmt = stmt.where(ONormChunk.dokument_id.in_(dokument_ids))
    elif norm_nummer:
        stmt = stmt.join(ONormDokument).where(ONormDokument.norm_nummer == norm_nummer)

    stmt = stmt.limit(top_k)

    result = await db.execute(stmt)
    return list(result.scalars().all())
