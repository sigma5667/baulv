"""Tageszähler für Kostenbremsen (Audit 2026-11).

Eine Zeile pro ``(counter_key, day)``. Liegt in der Datenbank statt im
Prozess-Speicher, damit der Zähler Neustarts/Deploys übersteht und für
alle Worker gleich gilt. Genutzt für den globalen Support-Chat-
Tagesdeckel (``support_chat``) und das KI-Tageslimit pro Konto
(``ai:<user_id>``). Alte Tage räumt der Nightly-Cleanup weg.
"""

from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DailyUsageCounter(Base):
    __tablename__ = "daily_usage_counters"

    counter_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
