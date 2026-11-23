"""Hintergrund-Job einer Plananalyse (2026-11).

Die KI-Analyse lief bis 2026-11 direkt in der HTTP-Anfrage; Cloudflare
bricht Anfragen nach ~100 s ab (524), große/gekachelte Pläne dauern
länger. Jetzt startet ``POST /plans/{id}/analyze`` einen Job im selben
Prozess (asyncio-Task, kein Redis/keine Queue) und das Frontend fragt
``GET /plans/{id}/analysis`` ab.

Eigene Tabelle statt Spalten auf ``plans``: die Analyse hält während
des Laufs eine Schreibsperre auf der Plan-Zeile (ihre Transaktion wird
erst am Ende committet). Fortschritt + Heartbeat müssen zwischendurch
in kurzen eigenen Transaktionen sichtbar werden — das ginge auf der
gesperrten Zeile nicht.

``heartbeat_at`` macht Neustarts sicher: der laufende Job schreibt ihn
alle paar Sekunden; stirbt der Prozess hart (SIGKILL, OOM), bleibt er
stehen und der Job gilt nach ``STALE_AFTER_S`` als fehlgeschlagen.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlanAnalysisJob(Base):
    __tablename__ = "plan_analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # running | completed | failed
    status: Mapped[str] = mapped_column(String(20), default="running")
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    pages_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Ergebnis-Dict wie früher die synchrone Antwort (pages_analyzed,
    # rooms_extracted, page_errors, ggf. heights_matched …).
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # HTTP-Status, den die alte synchrone Route geliefert hätte (422/500).
    error_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
