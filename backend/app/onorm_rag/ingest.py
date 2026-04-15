"""Retired ÖNORM PDF ingestion.

This module historically extracted text from uploaded ÖNORM PDFs with
PyMuPDF and stored the result as ``ONormChunk`` rows for full-text search.
That flow has been removed because storing copyrighted ÖNORM text on our
servers is not compatible with Austrian Standards International's copyright
(see docstring on ``app/api/onorm.py``).

The public function is kept as a no-op stub so that any lingering imports
or background tasks don't crash during the transition. New calls are a
bug — they should be removed from the call site.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def ingest_onorm_pdf(dokument_id: UUID, db: AsyncSession) -> int:
    """No-op. Raises to make accidental callers loud rather than silent."""
    del dokument_id, db
    raise RuntimeError(
        "ÖNORM PDF ingestion has been removed for copyright reasons. "
        "Calculation rules are now hardcoded in "
        "app/calculation_engine/trades/. See app/api/onorm.py for details."
    )
