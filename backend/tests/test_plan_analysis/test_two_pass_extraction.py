"""Tests für die v24.4.6 Two-Pass-Raumerkennung.

Pure Helper-Tests — kein Anthropic-SDK-Mock, kein PDF, kein fitz-Document.
Wir prüfen nur die deterministische Logik:

  * ``_bbox_fail_safe_reason`` — entscheidet anhand der Haiku-BBox, ob
    der Two-Pass-Pfad genommen oder auf den Single-Pass-Fallback
    zurückgefallen wird.
  * ``_bbox_to_pdf_rect`` — Skalierungs-Mathe Low-Res-Pixel →
    PDF-Punkt + 5 %-Padding + Clamping auf Bildgrenzen.
  * ``_should_tile`` — physische PDF-Punkt-Schwelle für die
    2×2-Kachel-Entscheidung (DPI-unabhängig).
  * ``_tile_2x2_pillow`` — Pillow-Kachelung mit 5 %-Overlap.
  * ``_merge_tiled_results`` — Dedupe nach normalisiertem Raumnamen
    beim Zusammenführen mehrerer Kachel-Resultate.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# _bbox_fail_safe_reason — Fail-Safe-Entscheidung für Two-Pass vs. Fallback
# ---------------------------------------------------------------------------


def test_bbox_fail_safe_none_input():
    """bbox=None (Haiku-Parse hat geknallt) → bbox_parse_failed."""
    from app.plan_analysis.pipeline import _bbox_fail_safe_reason

    assert (
        _bbox_fail_safe_reason(None, image_width=1500, image_height=1000)
        == "bbox_parse_failed"
    )


def test_bbox_fail_safe_missing_fields():
    """Fehlende oder nicht-numerische Felder → bbox_invalid_shape."""
    from app.plan_analysis.pipeline import _bbox_fail_safe_reason

    assert (
        _bbox_fail_safe_reason(
            {"x": 0, "y": 0, "width": 500}, image_width=1500, image_height=1000
        )
        == "bbox_invalid_shape"
    )
    assert (
        _bbox_fail_safe_reason(
            {"x": "left", "y": 0, "width": 500, "height": 500},
            image_width=1500, image_height=1000,
        )
        == "bbox_invalid_shape"
    )


def test_bbox_fail_safe_out_of_bounds():
    """Negativ-Koords oder Box ragt aus dem Bild → bbox_out_of_bounds."""
    from app.plan_analysis.pipeline import _bbox_fail_safe_reason

    assert (
        _bbox_fail_safe_reason(
            {"x": -10, "y": 0, "width": 500, "height": 500},
            image_width=1500, image_height=1000,
        )
        == "bbox_out_of_bounds"
    )
    # x+width > image_width
    assert (
        _bbox_fail_safe_reason(
            {"x": 1200, "y": 0, "width": 500, "height": 500},
            image_width=1500, image_height=1000,
        )
        == "bbox_out_of_bounds"
    )


def test_bbox_fail_safe_too_large():
    """> 95 % Bildfläche → bbox_too_large (Plan war bereits 'nur Grundriss')."""
    from app.plan_analysis.pipeline import _bbox_fail_safe_reason

    # 1500×1000 = 1.5M; 1500*1000 = 100% → too_large
    assert (
        _bbox_fail_safe_reason(
            {"x": 0, "y": 0, "width": 1500, "height": 1000},
            image_width=1500, image_height=1000,
        )
        == "bbox_too_large"
    )


def test_bbox_fail_safe_too_small():
    """< 15 % Bildfläche → bbox_too_small (Fehl-Erkennung).

    Tobi's Anforderung: eine so winzige Box ist fast sicher eine
    Fehl-Erkennung, lieber Fallback auf Single-Pass als an einem
    Plankopf-Detail die ganze Räume-Extraktion zu versemmeln.
    """
    from app.plan_analysis.pipeline import _bbox_fail_safe_reason

    # 100×100 = 10000 px², total 1.5M → ~0.67% → too_small
    assert (
        _bbox_fail_safe_reason(
            {"x": 0, "y": 0, "width": 100, "height": 100},
            image_width=1500, image_height=1000,
        )
        == "bbox_too_small"
    )


def test_bbox_fail_safe_usable():
    """Gesunde Mittelfeld-Box → None (= Two-Pass laufen lassen)."""
    from app.plan_analysis.pipeline import _bbox_fail_safe_reason

    # 600×600 / (1500*1000) = 24 % → usable (zwischen 15 % und 95 %)
    assert (
        _bbox_fail_safe_reason(
            {"x": 200, "y": 200, "width": 600, "height": 600},
            image_width=1500, image_height=1000,
        )
        is None
    )
    # 900×900 / (1500*1500) = 36 % auf Quadrat-Bild
    assert (
        _bbox_fail_safe_reason(
            {"x": 300, "y": 300, "width": 900, "height": 900},
            image_width=1500, image_height=1500,
        )
        is None
    )


# ---------------------------------------------------------------------------
# _compute_clip_rect_tuple — Pure-Math-Backbone von _bbox_to_pdf_rect
# (Tests laufen ohne fitz-Import, damit sie auch unter Windows-
# AppLocker / restriktiven DLL-Policies fahren können.)
# ---------------------------------------------------------------------------


def test_compute_clip_rect_identity_scale_no_padding():
    """1:1-Skalierung (source-px == pdf-pt), 0-Padding: Box bleibt
    wie sie ist."""
    from app.plan_analysis.pipeline import _compute_clip_rect_tuple

    x0, y0, x1, y1 = _compute_clip_rect_tuple(
        {"x": 100, "y": 200, "width": 300, "height": 400},
        source_width_px=1000, source_height_px=1000,
        pdf_width_pts=1000.0, pdf_height_pts=1000.0,
        padding_frac=0.0,
    )
    assert x0 == pytest.approx(100.0)
    assert y0 == pytest.approx(200.0)
    assert x1 == pytest.approx(400.0)
    assert y1 == pytest.approx(600.0)


def test_compute_clip_rect_half_scale():
    """source-px = 2 × pdf-pt → Koords werden halbiert."""
    from app.plan_analysis.pipeline import _compute_clip_rect_tuple

    x0, y0, x1, y1 = _compute_clip_rect_tuple(
        {"x": 200, "y": 400, "width": 600, "height": 800},
        source_width_px=2000, source_height_px=2000,
        pdf_width_pts=1000.0, pdf_height_pts=1000.0,
        padding_frac=0.0,
    )
    assert x0 == pytest.approx(100.0)
    assert y0 == pytest.approx(200.0)
    assert x1 == pytest.approx(400.0)
    assert y1 == pytest.approx(600.0)


def test_compute_clip_rect_padding_clamps_to_bounds():
    """5 %-Padding darf nicht über die Bildgrenzen hinausschießen.
    BBox am linken Rand: x=0, padding würde negativ werden → muss
    auf 0 clamped sein."""
    from app.plan_analysis.pipeline import _compute_clip_rect_tuple

    x0, y0, x1, y1 = _compute_clip_rect_tuple(
        {"x": 0, "y": 0, "width": 500, "height": 500},
        source_width_px=1000, source_height_px=1000,
        pdf_width_pts=1000.0, pdf_height_pts=1000.0,
        padding_frac=0.05,
    )
    # Linke + obere Kante geclampt auf 0.
    assert x0 == pytest.approx(0.0)
    assert y0 == pytest.approx(0.0)
    # Rechte + untere Kante mit 5 % Padding: 500 + 25 = 525.
    assert x1 == pytest.approx(525.0)
    assert y1 == pytest.approx(525.0)


def test_compute_clip_rect_padding_clamps_to_far_edge():
    """5 %-Padding am rechten/unteren Rand darf nicht über
    source_width/height hinaus."""
    from app.plan_analysis.pipeline import _compute_clip_rect_tuple

    x0, y0, x1, y1 = _compute_clip_rect_tuple(
        {"x": 600, "y": 600, "width": 400, "height": 400},
        source_width_px=1000, source_height_px=1000,
        pdf_width_pts=1000.0, pdf_height_pts=1000.0,
        padding_frac=0.05,
    )
    # 5 % von 400 = 20 px Padding, links/oben würde 580 ergeben.
    assert x0 == pytest.approx(580.0)
    assert y0 == pytest.approx(580.0)
    # Rechts/unten: 600+400+20 = 1020 → muss auf 1000 clamped sein.
    assert x1 == pytest.approx(1000.0)
    assert y1 == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# _should_tile — physische pt-Schwelle (DPI-unabhängig)
#
# Die Funktion liest nur ``.width`` und ``.height`` vom übergebenen
# Rect-Objekt → Duck-Type-Stub via SimpleNamespace ist legitim und
# macht die Tests fitz-frei (Windows-AppLocker-freundlich).
# ---------------------------------------------------------------------------


def _rect(width: float, height: float):
    """Duck-type stub: ein Objekt mit .width und .height, wie
    fitz.Rect.width / .height — alles, was ``_should_tile`` liest."""
    from types import SimpleNamespace

    return SimpleNamespace(width=width, height=height)


def test_should_tile_below_threshold():
    """Long-Edge < 2300 pt → False."""
    from app.plan_analysis.pipeline import _should_tile

    assert _should_tile(clip_rect=_rect(1500.0, 1500.0)) is False


def test_should_tile_above_threshold_via_width():
    """Long-Edge in width > 2300 pt → True (selbst bei kleiner Höhe)."""
    from app.plan_analysis.pipeline import _should_tile

    assert _should_tile(clip_rect=_rect(2400.0, 800.0)) is True


def test_should_tile_at_exact_threshold_is_false():
    """Strict greater-than: genau auf der Schwelle → False (kachelt nicht)."""
    from app.plan_analysis.pipeline import (
        _TILE_THRESHOLD_LONG_EDGE_PT,
        _should_tile,
    )

    assert (
        _should_tile(
            clip_rect=_rect(_TILE_THRESHOLD_LONG_EDGE_PT, 100.0)
        )
        is False
    )


def test_should_tile_threshold_constant_is_at_about_81cm():
    """Lock the constant — eine versehentliche Halbierung würde dafür
    sorgen, dass plötzlich JEDES A3-Crop gekachelt würde. 2300 pt ≈
    81 cm; das sollte unter A1, aber über A2 liegen."""
    from app.plan_analysis.pipeline import _TILE_THRESHOLD_LONG_EDGE_PT

    assert _TILE_THRESHOLD_LONG_EDGE_PT == 2300.0


def test_should_tile_dpi_independence():
    """Kernpunkt der Korrektur 1: Der Threshold ist in pt, nicht in
    Pixeln — d.h. eine Verdopplung des Render-DPI ändert die
    Entscheidung NICHT, weil die Clip-Rect-Geometrie in PDF-Punkten
    angegeben ist."""
    from app.plan_analysis.pipeline import _should_tile

    # Eine Crop-Region von 2000 pt Long-Edge. Egal ob ich später mit
    # 150, 300 oder 600 DPI rendere — die Entscheidung bleibt False.
    rect = _rect(2000.0, 1000.0)
    assert _should_tile(clip_rect=rect) is False


# ---------------------------------------------------------------------------
# _tile_2x2_pillow — 2×2 Kachelung mit 5 % Overlap
# ---------------------------------------------------------------------------


def _make_synth_jpeg(width: int, height: int, color: tuple = (240, 240, 240)) -> bytes:
    """Synthetisches JPEG-Bild für Pillow-Tests."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_tile_2x2_returns_four_tiles():
    """4 Kacheln in der erwarteten Reihenfolge [TL, TR, BL, BR]."""
    from app.plan_analysis.pipeline import _tile_2x2_pillow

    src = _make_synth_jpeg(1000, 800)
    tiles = _tile_2x2_pillow(src)
    assert len(tiles) == 4
    # Jede Kachel muss ein gültiges JPEG sein.
    for t in tiles:
        with Image.open(io.BytesIO(t)) as img:
            assert img.format == "JPEG"


def test_tile_2x2_with_overlap_has_expected_dims():
    """Mit 5 % Overlap auf 1000×800-Bild: jede Kachel ist ~525×420.
    Plus die Boxen müssen das Original abdecken (Sum > Original)."""
    from app.plan_analysis.pipeline import _tile_2x2_pillow

    src = _make_synth_jpeg(1000, 800)
    tiles = _tile_2x2_pillow(src, overlap_frac=0.05)

    # TL-Kachel: 0..(500+50) × 0..(400+40) = 550×440 in box-Koords,
    # aber Pillow.crop gibt das vollständige Sub-Bild zurück.
    with Image.open(io.BytesIO(tiles[0])) as tl:
        assert tl.width == 550   # 500 + 5%*1000
        assert tl.height == 440  # 400 + 5%*800

    with Image.open(io.BytesIO(tiles[1])) as tr:
        # TR-Kachel: (500-50)..1000 × 0..(400+40) = 550×440
        assert tr.width == 550
        assert tr.height == 440


def test_tile_2x2_no_overlap_exact_split():
    """Overlap=0: jede Kachel ist exakt ein Viertel des Originals."""
    from app.plan_analysis.pipeline import _tile_2x2_pillow

    src = _make_synth_jpeg(1000, 800)
    tiles = _tile_2x2_pillow(src, overlap_frac=0.0)
    with Image.open(io.BytesIO(tiles[0])) as tl:
        assert tl.width == 500
        assert tl.height == 400


# ---------------------------------------------------------------------------
# _merge_tiled_results — Dedupe per Raumnamen über Kacheln hinweg
# ---------------------------------------------------------------------------


def test_merge_empty_tiles_returns_units_empty():
    """Alle 4 Kacheln None / leer → leeres units-Array, notes optional."""
    from app.plan_analysis.pipeline import _merge_tiled_results

    merged = _merge_tiled_results([None, None, None, None])
    assert merged["units"] == []


def test_merge_dedupes_duplicate_room_name_across_tiles():
    """Selber Raumname in 2 Kacheln (z.B. wegen Overlap) → 1× im Output."""
    from app.plan_analysis.pipeline import (
        _count_extracted_rooms,
        _merge_tiled_results,
    )

    tile_a = {
        "floor_name": "EG",
        "units": [
            {"unit_name": "W1", "rooms": [
                {"room_name": "WOHNEN / KOCHEN", "area_m2": 32.84},
            ]},
        ],
    }
    tile_b = {
        "floor_name": "EG",
        "units": [
            {"unit_name": "W1", "rooms": [
                {"room_name": "WOHNEN / KOCHEN", "area_m2": 32.84},
                {"room_name": "BAD", "area_m2": 6.5},
            ]},
        ],
    }
    merged = _merge_tiled_results([tile_a, tile_b, None, None])
    assert _count_extracted_rooms(merged) == 2


def test_merge_dedupe_is_case_and_whitespace_insensitive():
    """``WOHNEN`` und ``wohnen   `` und ``Wohnen `` zählen als 1 Raum."""
    from app.plan_analysis.pipeline import (
        _count_extracted_rooms,
        _merge_tiled_results,
    )

    tile_a = {"units": [{"rooms": [{"room_name": "WOHNEN / KOCHEN"}]}]}
    tile_b = {"units": [{"rooms": [{"room_name": "  wohnen / kochen  "}]}]}
    tile_c = {"units": [{"rooms": [{"room_name": "Wohnen  /  Kochen"}]}]}
    merged = _merge_tiled_results([tile_a, tile_b, tile_c, None])
    assert _count_extracted_rooms(merged) == 1


def test_merge_collects_notes_only_when_no_rooms():
    """Wenn die Kachel-Summe 0 Räume liefert, sollen die ``notes``
    der Kacheln zusammengeführt werden (für die User-Fehlermeldung).
    Wenn dagegen ≥ 1 Raum erkannt wurde, ist ein einzelnes Kachel-
    ``notes`` (z.B. 'Plankopf-Kachel leer') kein 'Plan-Problem' und
    landet NICHT im merged notes-Feld."""
    from app.plan_analysis.pipeline import _merge_tiled_results

    # Fall A: alle Kacheln leer, aber 2 haben notes
    tile_a = {"units": [], "notes": "Plankopf-Kachel"}
    tile_b = {"units": [], "notes": "Garten-Kachel"}
    merged_a = _merge_tiled_results([tile_a, tile_b, None, None])
    assert merged_a["units"] == []
    assert "Plankopf-Kachel" in merged_a["notes"]
    assert "Garten-Kachel" in merged_a["notes"]

    # Fall B: eine Kachel hat 1 Raum + notes, andere leer.
    # → notes wird NICHT in den Output gehoben.
    tile_with_room = {
        "units": [{"rooms": [{"room_name": "Bad"}]}],
    }
    tile_empty_with_notes = {"units": [], "notes": "Lageplan-Kachel"}
    merged_b = _merge_tiled_results(
        [tile_with_room, tile_empty_with_notes, None, None]
    )
    assert "notes" not in merged_b or merged_b.get("notes") is None
