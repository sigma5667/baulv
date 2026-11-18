import uuid
from pathlib import Path
from datetime import datetime

from sqlalchemy import String, Integer, BigInteger, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(1000))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    plan_type: Mapped[str | None] = mapped_column(String(50))
    analysis_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="plans")

    @property
    def file_available(self) -> bool:
        """Liegt die hochgeladene Datei noch auf dem Server?

        Pläne aus der Zeit ohne dauerhaften Speicher (Railway-Container
        ohne Volume, bis 2026-11) haben ihre Datei bei einem Redeploy
        verloren. Die DB-Zeile und die extrahierten Räume bleiben —
        die App kennzeichnet solche Pläne statt sie zu löschen.
        """
        if not self.file_path:
            return False
        try:
            return Path(self.file_path).is_file()
        except OSError:
            return False


from app.db.models.project import Project  # noqa: E402
