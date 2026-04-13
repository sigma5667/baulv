import uuid
from datetime import datetime

from sqlalchemy import String, Text, Numeric, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.onorm import lv_onorm_selection, ONormDokument


class Leistungsverzeichnis(Base):
    __tablename__ = "leistungsverzeichnisse"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    trade: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="draft")
    onorm_basis: Mapped[str | None] = mapped_column(String(100))
    vorbemerkungen: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="leistungsverzeichnisse")
    gruppen: Mapped[list["Leistungsgruppe"]] = relationship(back_populates="lv", cascade="all, delete-orphan")
    selected_onorms: Mapped[list["ONormDokument"]] = relationship(secondary=lv_onorm_selection)


class Leistungsgruppe(Base):
    __tablename__ = "leistungsgruppen"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lv_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leistungsverzeichnisse.id", ondelete="CASCADE"))
    nummer: Mapped[str] = mapped_column(String(20))
    bezeichnung: Mapped[str] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    lv: Mapped["Leistungsverzeichnis"] = relationship(back_populates="gruppen")
    positionen: Mapped[list["Position"]] = relationship(back_populates="gruppe", cascade="all, delete-orphan")


class Position(Base):
    __tablename__ = "positionen"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    gruppe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leistungsgruppen.id", ondelete="CASCADE"))
    positions_nummer: Mapped[str] = mapped_column(String(20))
    kurztext: Mapped[str] = mapped_column(String(500))
    langtext: Mapped[str | None] = mapped_column(Text)
    einheit: Mapped[str] = mapped_column(String(20))
    menge: Mapped[float | None] = mapped_column(Numeric(12, 3))
    einheitspreis: Mapped[float | None] = mapped_column(Numeric(12, 2))
    positionsart: Mapped[str] = mapped_column(String(50), default="normal")
    text_source: Mapped[str] = mapped_column(String(50), default="ai")
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    gruppe: Mapped["Leistungsgruppe"] = relationship(back_populates="positionen")
    berechnungsnachweise: Mapped[list["Berechnungsnachweis"]] = relationship(back_populates="position", cascade="all, delete-orphan")

    @property
    def gesamtpreis(self) -> float | None:
        if self.menge is not None and self.einheitspreis is not None:
            return float(self.menge) * float(self.einheitspreis)
        return None


from app.db.models.project import Project  # noqa: E402
from app.db.models.calculation import Berechnungsnachweis  # noqa: E402
