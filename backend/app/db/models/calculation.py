import uuid
from datetime import datetime

from sqlalchemy import String, Text, Numeric, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Berechnungsnachweis(Base):
    """Traceable calculation proof for each position per room.

    Every calculated quantity must show: which room, which formula,
    which ÖNORM factor, and the result. This is the core traceability
    requirement of the system.
    """
    __tablename__ = "berechnungsnachweise"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("positionen.id", ondelete="CASCADE"))
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))

    # The traceable calculation
    raw_quantity: Mapped[float] = mapped_column(Numeric(12, 3))
    formula_description: Mapped[str] = mapped_column(Text)
    formula_expression: Mapped[str] = mapped_column(Text)
    onorm_factor: Mapped[float] = mapped_column(Numeric(8, 4), default=1.0)
    onorm_rule_ref: Mapped[str | None] = mapped_column(String(100))
    onorm_paragraph: Mapped[str | None] = mapped_column(String(100))
    deductions: Mapped[dict] = mapped_column(JSONB, default=list)
    net_quantity: Mapped[float] = mapped_column(Numeric(12, 3))
    unit: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    position: Mapped["Position"] = relationship(back_populates="berechnungsnachweise")
    room: Mapped["Room"] = relationship()


from app.db.models.lv import Position  # noqa: E402
from app.db.models.project import Room  # noqa: E402
