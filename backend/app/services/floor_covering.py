"""v24.4 — Bodenbelag-Normalisierung und Standard-Werte.

Hintergrund
===========

``Room.floor_type`` existiert seit v1.0 als ``String(100), nullable``
und wird sowohl von Vision (room_extraction-Prompt, Zeile 119+) als
auch von manuellen Raumanlagen befüllt. Pre-v24.4 war das Feld
Free-Text — KI lieferte ``"PARKETT"``, ``"FEINSTEINZEUG"``,
``"FLIESEN"``, ``"VINYL"``, ``"HOLZ"``, manuelle Eingabe ``"parkett"``
oder ``"Eichen-Dielen"``. Jede Aggregation nach Belag zerfaserte.

v24.4 führt eine kanonische Slug-Whitelist ein. ``normalise_floor_covering``
mappt sowohl Vision-Großschreibung als auch typische Branchen-Synonyme
auf einen der neun Standard-Slugs. Unbekannte Strings bleiben als
Free-Text stehen — das ist die Backward-Compatibility-Garantie für
Bestandsdaten und der Auffang für „sonstiges" (Freitext aus der UI).

Wird genutzt von
================

  * ``backend/app/api/rooms.py`` — beim ``create_room`` /
    ``update_room`` wird der eingehende ``floor_type`` durch
    ``normalise_floor_covering`` geschleust, damit neue Werte
    konsistent landen.
  * ``backend/app/export/mengenermittlung_pdf.py`` — die
    Aggregations-Sektion gruppiert per Slug, wendet die Normalisierung
    auf Bestandsdaten zur Laufzeit an.
  * ``backend/app/plan_analysis/pipeline.py`` — Vision-Output wird
    bei der Persistierung normalisiert (Slug landet in der DB statt
    "PARKETT").
  * ``frontend/src/components/room/FloorCoveringSelect.tsx`` — das
    Dropdown listet die ``FLOOR_COVERING_LABELS`` als Optionen.

Slug vs. Display-Label
======================

* Slug: lowercase ASCII, single token (``parkett``). Wird in der DB
  gespeichert und in Aggregationen als Gruppen-Key verwendet.
* Label: User-facing deutscher Anzeigename (``Parkett``). Wird im PDF
  und im Frontend gerendert. Niemals in der DB.

Free-Text-Werte (z.B. ``"Designboden Marke X"``) bleiben als-is und
werden in der PDF-Aggregation in ihrer eigenen Gruppe gezählt. Das ist
nicht ideal aber besser als sie auf einen vermeintlich passenden Slug
zu zwingen.
"""

from __future__ import annotations


# Kanonische Slugs. Reihenfolge entspricht der UX-Reihenfolge im
# Frontend-Dropdown (häufige zuerst). ``sonstiges`` ist der Sentinel-
# Wert den die UI in ein Freitext-Feld umschaltet — er landet niemals
# selbst in der DB, sondern wird durch den Freitext ersetzt.
FLOOR_COVERING_SLUGS: tuple[str, ...] = (
    "parkett",
    "fliesen",
    "laminat",
    "vinyl",
    "teppich",
    "linoleum",
    "naturstein",
    "beton",
    "estrich",
)


# Slug → User-facing deutsches Anzeige-Label. Wird im PDF und im
# Frontend-Dropdown gerendert. Die UI mappt Label↔Slug rein clientseitig
# über diese Tabelle (kein Roundtrip).
FLOOR_COVERING_LABELS: dict[str, str] = {
    "parkett": "Parkett",
    "fliesen": "Fliesen",
    "laminat": "Laminat",
    "vinyl": "Vinyl",
    "teppich": "Teppich",
    "linoleum": "Linoleum",
    "naturstein": "Naturstein",
    "beton": "Beton",
    "estrich": "Estrich",
}


# Branchen-Synonyme → kanonischer Slug. Wird vom Normaliser benutzt
# um Vision-Großschreibung und typische Architekten-Bezeichnungen auf
# die Slug-Liste zu mappen.
#
# Match-Strategie (siehe ``normalise_floor_covering``):
#   1. Volltreffer auf den lowercased String → Slug.
#   2. Substring-Match: wenn eine der Synonyme als Substring im
#      lowercased Input vorkommt → Slug. Das fängt "Eichen-Parkett",
#      "Massiv-Parkett", "Holzparkett" alles auf "parkett".
#
# Wichtig: die Match-Reihenfolge ist DETERMINISTISCH wegen dict-
# insertion-order in Python 3.7+. Speziellere/längere Patterns
# stehen vor allgemeineren, damit "Feinsteinzeug" nicht
# versehentlich auf "Stein"→"naturstein" matched.
_SYNONYM_TO_SLUG: dict[str, str] = {
    # Fliesen-Varianten zuerst, damit "Feinsteinzeug" nicht durch
    # spätere Stein-Regeln fälschlich auf "naturstein" geht.
    "feinsteinzeug": "fliesen",
    "fliesen": "fliesen",
    "fliese": "fliesen",
    "kacheln": "fliesen",
    "keramik": "fliesen",
    # Parkett — fängt Eichen-Parkett, Massiv-Parkett, Holzparkett etc.
    "parkett": "parkett",
    "dielen": "parkett",
    "holzboden": "parkett",
    # Holz allein matched zuletzt damit "Holzboden" Vorrang hat.
    "holz": "parkett",
    # Laminat
    "laminat": "laminat",
    # Vinyl / PVC — chemisch identisches Material, PVC ist nur das
    # Roh-Polymer. v24.4.1 — Designboden wurde aus dieser Mapping-
    # Gruppe entfernt (war in v24.4 fälschlich auf "vinyl" gemapped).
    # Begründung: "Designboden" ist ein eigenständiger Branchenbegriff
    # für mehrschichtige Click-Bodensysteme und meint nicht zwingend
    # Vinyl — Architekten verwenden den Begriff auch für Linoleum-
    # oder HDF-basierte Konstruktionen. User die "Designboden" via
    # Sonstiges-Freitext eintragen wollen das als eigene Kategorie
    # in der Mengenermittlungs-Aggregation sehen, nicht zu Vinyl
    # zusammengeworfen.
    "vinyl": "vinyl",
    "pvc": "vinyl",
    # Teppich
    "teppich": "teppich",
    "teppichboden": "teppich",
    # Linoleum
    "linoleum": "linoleum",
    "lino": "linoleum",
    # Naturstein-Varianten — Granit, Marmor, Schiefer als Sammelbegriff.
    "naturstein": "naturstein",
    "granit": "naturstein",
    "marmor": "naturstein",
    "schiefer": "naturstein",
    # "stein" allein ist sehr generisch; matched zuletzt.
    "stein": "naturstein",
    # Beton / Sichtbeton / Industrieboden
    "sichtbeton": "beton",
    "beton": "beton",
    "industrieboden": "beton",
    # Estrich (als sichtbarer Belag, nicht als Unterboden)
    "estrich": "estrich",
}


def normalise_floor_covering(raw: str | None) -> str | None:
    """Map a free-form ``floor_type`` string to one of the canonical
    slugs from ``FLOOR_COVERING_SLUGS``.

    Returns
    -------
    str | None
        * ``None`` → input was ``None``, empty, or whitespace only.
        * one of the slugs in ``FLOOR_COVERING_SLUGS`` → match found.
        * the original ``raw`` value (cleaned of leading/trailing
          whitespace) → no known synonym matched. Backward-compat
          for legacy free-text values like ``"Designboden Marke X"``
          and for the ``sonstiges``-Freitext path from the UI.

    Algorithm
    ---------
    1. Strip whitespace, lower-case.
    2. Exact match against ``_SYNONYM_TO_SLUG`` keys.
    3. Substring match: walk ``_SYNONYM_TO_SLUG`` in insertion order
       (specific-first), return the first slug whose key appears as
       a substring in the lowered input. Insertion order is set up
       so "Feinsteinzeug" matches "fliesen" before "stein" could
       incorrectly grab it for "naturstein".
    4. No match → return the original string (whitespace-stripped).
       Caller may decide to store it as a custom free-text value.
    """
    if raw is None:
        return None
    cleaned = str(raw).strip()
    if not cleaned:
        return None

    lowered = cleaned.lower()

    # Step 2 — exact match.
    if lowered in _SYNONYM_TO_SLUG:
        return _SYNONYM_TO_SLUG[lowered]

    # Step 3 — substring match in declared (insertion) order.
    for synonym, slug in _SYNONYM_TO_SLUG.items():
        if synonym in lowered:
            return slug

    # Step 4 — return the cleaned original. Calling code (rooms.py,
    # mengenermittlung_pdf.py) treats this as a custom value and lets
    # it through unchanged.
    return cleaned


def display_label(slug_or_freetext: str | None) -> str:
    """Return the user-facing German label for a floor-covering value.

    * Canonical slug → look up in ``FLOOR_COVERING_LABELS``
      (e.g. ``"parkett"`` → ``"Parkett"``).
    * Free-text fallback (anything not in the slug set) → return as-is
      so legacy ``"Designboden Marke X"``-style values still render
      readably in the PDF and the UI.
    * ``None`` / empty → ``"Räume ohne Belag-Angabe"``.
      v24.4.1 — vorher ``"Nicht klassifiziert"``; umbenannt damit der
      Wortlaut konsistent mit den anderen Lücken-Hinweisen im PDF
      ist und Vater nicht raten muss was die Pille meint.
    """
    if slug_or_freetext is None or not str(slug_or_freetext).strip():
        return "Räume ohne Belag-Angabe"
    s = str(slug_or_freetext).strip()
    return FLOOR_COVERING_LABELS.get(s, s)
