"""Tests for the v24.4 floor-covering normaliser.

Lock the synonym-mapping table down so a future refactor (e.g. adding
new slugs, removing entries) can't accidentally regress the Vision
or manual-edit normalisation. Pure-function tests, no DB.

The normaliser is the foundation of the v24.4 Bodenflächen-Aggregation
in the PDF: when grouping rooms by their ``floor_type``, we need
``"PARKETT"``, ``"Parkett"``, ``"parkett"`` and ``"Eichen-Parkett"``
to all land on the same bucket. Without this normaliser the
aggregation zerfasert and the Bauträger sees four "Parkett"-Varianten
in his PDF.
"""

from __future__ import annotations

import pytest

from app.services.floor_covering import (
    FLOOR_COVERING_LABELS,
    FLOOR_COVERING_SLUGS,
    display_label,
    normalise_floor_covering,
)


# ---------------------------------------------------------------------------
# Slug-Liste und Label-Tabelle bleiben konsistent
# ---------------------------------------------------------------------------


def test_slugs_and_labels_are_consistent():
    """Jeder Slug aus ``FLOOR_COVERING_SLUGS`` hat ein passendes
    Label in ``FLOOR_COVERING_LABELS`` — und umgekehrt keine
    Waisen-Labels ohne Slug. Locking diese 1:1-Beziehung
    explizit so dass ein future "neuer Slug ohne Label hinzufügen"-
    Diff die Test-Suite rot färbt."""
    assert set(FLOOR_COVERING_SLUGS) == set(FLOOR_COVERING_LABELS.keys())


# ---------------------------------------------------------------------------
# Volltreffer-Normalisierung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Lowercase passt direkt durch.
        ("parkett", "parkett"),
        ("fliesen", "fliesen"),
        # Vision-Großschreibung — der dominante Real-World-Fall.
        ("PARKETT", "parkett"),
        ("FLIESEN", "fliesen"),
        ("FEINSTEINZEUG", "fliesen"),
        ("VINYL", "vinyl"),
        ("HOLZ", "parkett"),  # via "holz" → "parkett"
        # Mixed-Case
        ("Parkett", "parkett"),
        ("Feinsteinzeug", "fliesen"),
        # Whitespace abscheren
        ("  parkett  ", "parkett"),
        # Synonym-Substring
        ("Holzboden", "parkett"),
        ("Dielen", "parkett"),
        ("Teppichboden", "teppich"),
        ("Sichtbeton", "beton"),
        ("Marmor", "naturstein"),
        ("Granit", "naturstein"),
        # PVC → Vinyl (chemisch identisches Material).
        ("PVC", "vinyl"),
        # NB: "Designboden" wurde in v24.4.1 aus der Synonym-Map
        # entfernt — siehe ``test_designboden_stays_freetext`` unten.
    ],
)
def test_known_synonyms_map_to_canonical_slugs(raw, expected):
    assert normalise_floor_covering(raw) == expected


# ---------------------------------------------------------------------------
# Substring-Match-Vorrang
# ---------------------------------------------------------------------------


def test_eichen_parkett_substring_matches_parkett():
    """Architekten schreiben oft Holzart als Präfix ("Eichen-Parkett",
    "Buche-Parkett"). Der Substring-Match muss das auf ``parkett``
    mappen, nicht auf den Free-Text-Fallback."""
    assert normalise_floor_covering("Eichen-Parkett") == "parkett"
    assert normalise_floor_covering("Buche Parkett") == "parkett"


def test_feinsteinzeug_does_not_match_stein():
    """Reihenfolge im Synonym-Dict matters: "Feinsteinzeug" steht
    vor "stein" in der insertion order, damit es zu ``fliesen``
    geht statt fälschlich zu ``naturstein``."""
    assert normalise_floor_covering("Feinsteinzeug") == "fliesen"
    assert normalise_floor_covering("FEINSTEINZEUG") == "fliesen"


# ---------------------------------------------------------------------------
# Free-Text-Fallback
# ---------------------------------------------------------------------------


def test_unknown_string_returns_cleaned_original():
    """Unbekannte Free-Text-Werte (z.B. aus dem "sonstiges"-Pfad
    der UI) bleiben unverändert stehen — Backward-Compat für
    Bestandsdaten und der Auffang für vom User getipte Marken-
    namen die wir nicht kennen können."""
    assert normalise_floor_covering("Designboden Marke X") == "Designboden Marke X"
    assert normalise_floor_covering("  Custom-Belag  ") == "Custom-Belag"


def test_designboden_stays_freetext():
    """v24.4.1 — Bug-Fix-Regression-Test.

    Pre-v24.4.1 hatte die Synonym-Map "designboden" → "vinyl"
    enthalten. Effekt: User tippt im Sonstiges-Freitext-Pfad
    "Designboden" → Backend normalisiert zu "vinyl" → PDF gruppiert
    den Raum als Vinyl statt als eigene Kategorie. Vater meldete
    das als kritisch ("Feature ist sonst unbrauchbar").

    Der Fix entfernt "designboden" aus der Synonym-Map. Locking
    den Zustand so dass eine künftige "defensive Re-Addition"
    sofort rot wird."""
    assert normalise_floor_covering("Designboden") == "Designboden"
    assert normalise_floor_covering("designboden") == "designboden"
    assert normalise_floor_covering("DESIGNBODEN") == "DESIGNBODEN"


# ---------------------------------------------------------------------------
# Null und Leer-Werte
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
def test_empty_inputs_return_none(raw):
    """None / Whitespace-only → None. Damit landet im DB-Feld
    NULL statt eines leeren Strings, was die PDF-Aggregation
    sauber als "nicht klassifiziert" behandeln kann."""
    assert normalise_floor_covering(raw) is None


# ---------------------------------------------------------------------------
# display_label
# ---------------------------------------------------------------------------


def test_display_label_for_known_slug():
    assert display_label("parkett") == "Parkett"
    assert display_label("naturstein") == "Naturstein"


def test_display_label_for_freetext_passes_through():
    """Custom Free-Text-Werte werden unverändert angezeigt —
    das PDF / die UI sollen lesbar bleiben auch für Werte die
    nicht in der Slug-Liste sind."""
    assert display_label("Designboden Marke X") == "Designboden Marke X"


def test_display_label_for_none_is_raeume_ohne_belag_angabe():
    """v24.4.1 — Wortlaut umgestellt von "Nicht klassifiziert" auf
    "Räume ohne Belag-Angabe", damit der PDF-Hinweistext konsistent
    mit den anderen Mängel-Buckets bleibt."""
    assert display_label(None) == "Räume ohne Belag-Angabe"
    assert display_label("") == "Räume ohne Belag-Angabe"
    assert display_label("   ") == "Räume ohne Belag-Angabe"
