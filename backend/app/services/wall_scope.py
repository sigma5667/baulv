"""Welche Räume zählen in die Wandflächen-Summe? — EINE Regel.

Gilt gleichermaßen für die Wandberechnungs-Tabelle (Frontend,
``PlanAnalysisPage.countsForWallSum``), "Wandflächen übernehmen"
(``app/api/lv.sync_wall_areas``) und "LV berechnen"
(``MalerarbeitenCalculator``). Bis 2026-11 schloss nur die Tabelle
Balkone/Terrassen aus; das LV rechnete deren Wände mit.

Außenflächen ohne Innenwandbezug: Raumtyp ``balkon``/``terrasse`` oder
Name mit Balkon/Terrasse/Loggia/Garten (Loggia/Garten gibt es nicht als
Typ). Die Schlagwortliste ist zeichengleich mit dem Frontend — ein Test
pinnt beide Seiten. Aufzug ist bewusst NICHT enthalten (Fachregel offen).
"""

from __future__ import annotations

AUSSENFLAECHE_TYPES = ("balkon", "terrasse")
AUSSENFLAECHE_KEYWORDS = ("balkon", "terrasse", "loggia", "garten")


def is_aussenflaeche(name: str | None, room_type: str | None) -> bool:
    if (room_type or "").lower() in AUSSENFLAECHE_TYPES:
        return True
    lowered = (name or "").lower()
    return any(kw in lowered for kw in AUSSENFLAECHE_KEYWORDS)


def counts_for_wall_sum(room) -> bool:
    """Aktiv, kein Außenraum, echter Umfang > 0 (wie die Tabelle)."""
    if not getattr(room, "is_active", True):
        return False
    if is_aussenflaeche(getattr(room, "name", None), getattr(room, "room_type", None)):
        return False
    perimeter = getattr(room, "perimeter_m", None)
    return perimeter is not None and float(perimeter) > 0
