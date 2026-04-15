import uuid
from datetime import datetime, date

from sqlalchemy import String, Text, Boolean, Date, ForeignKey, DateTime, Table, Column
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# Many-to-many: which ÖNORMs are selected for a given LV.
#
# Historically the frontend allowed users to attach uploaded ÖNORM PDFs to
# an LV as a "knowledge base". That flow has been removed for copyright
# reasons (see ``app/api/onorm.py``), but the association table is kept so
# existing rows continue to resolve and the Leistungsverzeichnis model
# doesn't need a migration-visible schema change.
lv_onorm_selection = Table(
    "lv_onorm_selection",
    Base.metadata,
    Column("lv_id", PG_UUID(as_uuid=True), ForeignKey("leistungsverzeichnisse.id", ondelete="CASCADE"), primary_key=True),
    Column("onorm_dokument_id", PG_UUID(as_uuid=True), ForeignKey("onorm_dokumente.id", ondelete="CASCADE"), primary_key=True),
)


class ONormDokument(Base):
    """Lightweight ÖNORM registry entry.

    After the copyright-compliance refactor this table no longer references
    any stored PDF content. The ``file_path`` column and the ``ONormChunk``
    relationship have been removed. The remaining columns describe which
    ÖNORM an entry corresponds to (number, title, trade) — metadata only,
    not copyrightable content.
    """

    __tablename__ = "onorm_dokumente"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    norm_nummer: Mapped[str] = mapped_column(String(50))
    titel: Mapped[str | None] = mapped_column(String(500))
    trade: Mapped[str | None] = mapped_column(String(100))
    ausgabe_datum: Mapped[date | None] = mapped_column(Date)
    upload_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    regeln: Mapped[list["ONormRegel"]] = relationship(back_populates="dokument", cascade="all, delete-orphan")


class ONormRegel(Base):
    """Coded ÖNORM rules for deterministic calculation.

    These are NOT AI-generated and NOT copied from the ÖNORM text — they are
    manually coded references to mathematical formulas and parameters that
    the calculation engine uses for quantity determination. Mathematical
    formulas and algorithms are not copyrightable.
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
