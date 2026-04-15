"""Neutralized ÖNORM RAG retriever.

Since BauLV no longer stores copyrighted ÖNORM text on its servers (see the
docstring on ``app/api/onorm.py``), there is nothing to retrieve. This
module is kept only so existing callers in ``app/chat/assistant.py`` and
``app/lv_generator/generator.py`` don't have to be rewritten immediately —
they call ``search_onorm_chunks`` and handle an empty result gracefully.

Once those callers are cleaned up, this file can be deleted entirely.
"""

from uuid import UUID
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def search_onorm_chunks(
    query: str,
    db: AsyncSession,
    norm_nummer: str | None = None,
    dokument_ids: list[UUID] | None = None,
    top_k: int = 5,
) -> list[Any]:
    """Always return an empty list.

    Previous implementations performed a full-text search against stored
    ÖNORM chunks, which presupposed that the copyrighted text was on
    disk / in the database. That flow has been retired for legal reasons
    (copyright of Austrian Standards International).

    The caller signature is preserved so downstream code that expects a
    list of chunk-like objects keeps type-checking; callers already guard
    with ``if chunks:`` so an empty list is a safe no-op.
    """
    # Intentionally unused — kept so the public signature matches the
    # original RAG retriever and existing callers continue to type-check.
    del query, db, norm_nummer, dokument_ids, top_k
    return []
