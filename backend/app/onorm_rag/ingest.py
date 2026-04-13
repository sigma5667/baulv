"""ÖNORM PDF ingestion: extract text, chunk, and store for RAG."""

import re
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.onorm import ONormDokument, ONormChunk


async def ingest_onorm_pdf(dokument_id: UUID, db: AsyncSession) -> int:
    """Process an uploaded ÖNORM PDF: extract text, chunk, store.

    Returns number of chunks created.
    """
    dokument = await db.get(ONormDokument, dokument_id)
    if not dokument:
        raise ValueError(f"Dokument {dokument_id} not found")

    dokument.upload_status = "processing"
    await db.flush()

    try:
        # Extract text with PyMuPDF
        chunks = _extract_and_chunk(dokument.file_path)

        # Store chunks
        for chunk_data in chunks:
            chunk = ONormChunk(
                dokument_id=dokument_id,
                chunk_text=chunk_data["text"],
                section_number=chunk_data.get("section"),
                section_title=chunk_data.get("title"),
                page_number=chunk_data.get("page"),
            )
            db.add(chunk)

        dokument.upload_status = "completed"
        await db.flush()
        return len(chunks)

    except Exception:
        dokument.upload_status = "failed"
        await db.flush()
        raise


def _extract_and_chunk(file_path: str, max_chunk_tokens: int = 500) -> list[dict]:
    """Extract text from PDF and split into semantic chunks."""
    import fitz

    doc = fitz.open(file_path)
    chunks: list[dict] = []
    current_section = None
    current_title = None
    current_text = ""
    current_page = 1

    for page_num, page in enumerate(doc, 1):
        text = page.get_text()
        lines = text.split("\n")

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Detect section headers (e.g., "3.2.1 Wandflächen")
            section_match = re.match(r'^(\d+(?:\.\d+)*)\s+(.+)', stripped)
            if section_match:
                # Save previous chunk
                if current_text.strip():
                    chunks.append({
                        "text": current_text.strip(),
                        "section": current_section,
                        "title": current_title,
                        "page": current_page,
                    })

                current_section = section_match.group(1)
                current_title = section_match.group(2)
                current_text = stripped + "\n"
                current_page = page_num
            else:
                current_text += stripped + "\n"

                # Check if chunk is getting too large (rough token estimate)
                if len(current_text.split()) > max_chunk_tokens:
                    chunks.append({
                        "text": current_text.strip(),
                        "section": current_section,
                        "title": current_title,
                        "page": current_page,
                    })
                    current_text = ""

    # Save last chunk
    if current_text.strip():
        chunks.append({
            "text": current_text.strip(),
            "section": current_section,
            "title": current_title,
            "page": current_page,
        })

    doc.close()
    return chunks
