import uuid
from datetime import datetime, date

from sqlalchemy import String, Text, Integer, Boolean, Date, ForeignKey, DateTime, Table, Column
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None


# Many-to-many: which ÖNORMs are selected for a given LV
lv_onorm_selection = Table(
    "lv_onorm_selection",
    Base.metadata,
    Column("lv_id", PG_UUID(as_uuid=True), ForeignKey("leistungsverzeichnisse.id", ondelete="CASCADE"), primary_key=True),
    Column("onorm_dokument_id", PG_UUID(as_uuid=True), ForeignKey("onorm_dokumente.id", ondelete="CASCADE"), primary_key=True),
)


class ONormDokument(Base):
    __tablename__ = "onorm_dokumente"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    norm_nummer: Mapped[str] = mapped_column(String(50))
    titel: Mapped[str | None] = mapped_column(String(500))
    trade: Mapped[str | None] = mapped_column(String(100))
    ausgabe_datum: Mapped[date | None] = mapped_column(Date)
    file_path: Mapped[str | None] = mapped_column(String(1000))
    upload_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    chunks: Mapped[list["ONormChunk"]] = relationship(back_populates="dokument", cascade="all, delete-orphan")
    regeln: Mapped[list["ONormRegel"]] = relationship(back_populates="dokument", cascade="all, delete-orphan")


class ONormChunk(Base):
    __tablename__ = "onorm_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dokument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onorm_dokumente.id", ondelete="CASCADE"))
    chunk_text: Mapped[str] = mapped_column(Text)
    section_number: Mapped[str | None] = mapped_column(String(50))
    section_title: Mapped[str | None] = mapped_column(String(255))
    page_number: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)

    dokument: Mapped["ONormDokument"] = relationship(back_populates="chunks")


class ONormRegel(Base):
    """Coded ÖNORM rules for deterministic calculation.

    These are NOT AI-generated — they are manually coded or admin-verified rules
    that the calculation engine uses for quantity determination.
    """
    __tablename__ = "onorm_regeln"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dokument_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onorm_dokumente.id", ondelete="SET NULL"))
    regel_code: Mapped[str] = mapped_column(String(100), unique=True)
    trade: Mapped[str] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    description_de: Mapped[str] = mapped_column(Text)
    formula_type: Mapped[str | None] = mapped_column(String(50))
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    onorm_reference: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    dokument: Mapped["ONormDokument | None"] = relationship(back_populates="regeln")
