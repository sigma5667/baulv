import uuid
from datetime import datetime

from sqlalchemy import String, Text, Numeric, Boolean, Integer, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    client_name: Mapped[str | None] = mapped_column(String(255))
    project_number: Mapped[str | None] = mapped_column(String(100))
    grundstuecksnr: Mapped[str | None] = mapped_column(String(100))
    planverfasser: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    plans: Mapped[list["Plan"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    buildings: Mapped[list["Building"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    leistungsverzeichnisse: Mapped[list["Leistungsverzeichnis"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="project")


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="buildings")
    floors: Mapped[list["Floor"]] = relationship(back_populates="building", cascade="all, delete-orphan")


class Floor(Base):
    __tablename__ = "floors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    level_number: Mapped[int | None] = mapped_column(Integer)
    floor_height_m: Mapped[float | None] = mapped_column(Numeric(6, 3))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    building: Mapped["Building"] = relationship(back_populates="floors")
    units: Mapped[list["Unit"]] = relationship(back_populates="floor", cascade="all, delete-orphan")


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    floor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("floors.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    unit_type: Mapped[str | None] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    floor: Mapped["Floor"] = relationship(back_populates="units")
    rooms: Mapped[list["Room"]] = relationship(back_populates="unit", cascade="all, delete-orphan")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    name: Mapped[str] = mapped_column(String(255))
    room_number: Mapped[str | None] = mapped_column(String(50))
    room_type: Mapped[str | None] = mapped_column(String(100))
    area_m2: Mapped[float | None] = mapped_column(Numeric(10, 3))
    perimeter_m: Mapped[float | None] = mapped_column(Numeric(10, 3))
    # Provenance flag for ``perimeter_m`` so the UI can differentiate
    # an honest Vision extraction from a fallback estimate the
    # pipeline computed out of the room area. Values: ``vision`` (KI
    # extracted from plan), ``estimated`` (4·√area·1.10 fallback when
    # Vision returned nothing), ``manual`` (user typed it via
    # ``PUT /rooms/{id}``), NULL (legacy / unknown — pre-016 rows
    # without an inferable source). Frontend keys off this to show
    # the right hint without alarming the user when an estimate is
    # good enough.
    perimeter_source: Mapped[str | None] = mapped_column(String(20))
    height_m: Mapped[float | None] = mapped_column(Numeric(6, 3))
    floor_type: Mapped[str | None] = mapped_column(String(100))
    wall_type: Mapped[str | None] = mapped_column(String(100))
    ceiling_type: Mapped[str | None] = mapped_column(String(100))
    is_wet_room: Mapped[bool] = mapped_column(Boolean, default=False)
    has_dachschraege: Mapped[bool] = mapped_column(Boolean, default=False)
    is_staircase: Mapped[bool] = mapped_column(Boolean, default=False)
    # Where the ceiling height came from so the UI can warn when we've
    # fallen back to a default. Values: ``schnitt`` (from a cross-section
    # plan), ``grundriss`` (noted on the floorplan), ``manual`` (user
    # typed it in), ``default`` (assumed 2.50 m because nothing else was
    # available). Frontend highlights ``default`` rows in amber so the
    # user confirms or corrects before the number flows into the LV.
    ceiling_height_source: Mapped[str] = mapped_column(String(20), default="default")
    # Cached results from the wall-calculation service so the frontend
    # doesn't re-derive them on every render and the LV export can pick
    # them up directly. ``gross`` = perimeter × height × factor;
    # ``net`` = gross minus openings ≥ 2.5 m² (when
    # ``deductions_enabled``). Both nullable because a freshly imported
    # room has no calculation until the user runs it.
    wall_area_gross_m2: Mapped[float | None] = mapped_column(Numeric(10, 3))
    wall_area_net_m2: Mapped[float | None] = mapped_column(Numeric(10, 3))
    # The multiplier that was applied on the last calculation —
    # 1.0 for a normal room, 1.12 for 3–4 m ceilings, 1.16 for >4 m,
    # 1.5 for stairwells. Stored so the UI can show *which* factor
    # was applied without re-running the calculation logic.
    applied_factor: Mapped[float | None] = mapped_column(Numeric(4, 3))
    # User-controlled flag: if False the calculator treats all openings
    # as deducted = 0 and returns gross == net. Austrian practice is
    # "only subtract large openings (≥ 2.5 m²)"; turning this off is a
    # conservative override the estimator sometimes wants.
    deductions_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    ai_confidence: Mapped[float | None] = mapped_column(Numeric(4, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    unit: Mapped["Unit"] = relationship(back_populates="rooms")
    openings: Mapped[list["Opening"]] = relationship(back_populates="room", cascade="all, delete-orphan")


class Opening(Base):
    __tablename__ = "openings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    opening_type: Mapped[str] = mapped_column(String(50))
    width_m: Mapped[float] = mapped_column(Numeric(6, 3))
    height_m: Mapped[float] = mapped_column(Numeric(6, 3))
    count: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(50), default="manual")

    room: Mapped["Room"] = relationship(back_populates="openings")

    @property
    def area_m2(self) -> float:
        return float(self.width_m) * float(self.height_m) * self.count


# Forward references for relationships
from app.db.models.plan import Plan  # noqa: E402
from app.db.models.lv import Leistungsverzeichnis  # noqa: E402
from app.db.models.chat import ChatSession  # noqa: E402
