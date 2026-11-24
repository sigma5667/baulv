"""Wohnungs-Bezeichnung (TOP) für die Anzeige — nie erfinden.

Die Plananalyse legt Räume in Einheiten ab (``Unit.name``). Das kann eine
echte Wohnungsbezeichnung aus dem Plan sein ("TOP 5", "Top 1.2", "WHG 7",
"W1", "Wohnung 3") oder ein Behälter-Name ohne Aussage ("Einheit 1" als
Pipeline-Fallback, "Allgemein", "OG"). Angezeigt wird nur Ersteres;
alles andere ergibt ``None`` → die Oberfläche lässt die TOP leer.

``loaded_attr`` liest eine ORM-Beziehung nur, wenn sie bereits geladen
ist. Auf einer async-Session würde ein Lazy-Load außerhalb des Greenlets
mit ``MissingGreenlet`` (HTTP 500) abbrechen — dann lieber leer anzeigen.
"""

from __future__ import annotations

import re

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable

_UNIT_LABEL_RE = re.compile(
    r"^\s*(?:top|whg\.?|wohnung|w|tür|tuer)\s*\.?\s*\d+[a-z]?(?:\s*[./]\s*\d+)?\s*$",
    re.IGNORECASE,
)


def unit_label(name: str | None) -> str | None:
    """Echte Wohnungsbezeichnung oder ``None``."""
    if not name:
        return None
    cleaned = " ".join(str(name).split())
    return cleaned if _UNIT_LABEL_RE.match(cleaned) else None


def loaded_attr(obj, name: str):
    """``getattr`` nur für bereits geladene Beziehungen, sonst ``None``."""
    if obj is None:
        return None
    try:
        state = sa_inspect(obj)
    except NoInspectionAvailable:
        return getattr(obj, name, None)
    if name in state.unloaded:
        return None
    return getattr(obj, name, None)


def room_display(room_name: str | None, label: str | None) -> str:
    """"TOP 5 · Bad" bzw. nur "Bad"."""
    name = room_name or ""
    return f"{label} · {name}" if label and name else (name or label or "")
