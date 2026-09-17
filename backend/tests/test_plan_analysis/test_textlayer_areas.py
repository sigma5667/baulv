"""v24.9 — Schritt C: Raumflächen aus dem PDF-Textlayer.

Die Fälle sind echten Plänen nachgebaut:
  * GWM EP-04 EG — vier Wohnungen, jede mit "Vorraum"; Stempel tragen die
    Einheit ("TOP 2.1 | Vorraum | B: … | F: 2,93 m2"), Wohnungssummen stehen
    als "TOP 3 | 28,07m2" im Stiegenhaus.
  * 50.02 ERDGESCHOSS — zwei gespiegelte Wohnungen, Stempel OHNE Einheit,
    jeder Name doppelt.
  * TOP 1 — Raumtabelle am Blattrand wiederholt die Stempel.
"""

from __future__ import annotations

import copy

import pytest

from app.plan_analysis.textlayer_areas import (
    PageTextlayer,
    apply_report,
    assess_trust,
    assign_areas,
    parse_name,
    parse_stamps,
)


# ---------------------------------------------------------------------------
# Helfer: page.get_text("dict")-Nachbau
# ---------------------------------------------------------------------------


def _block(lines, x=100.0, y=100.0, lh=10.0, w=60.0):
    return {
        "type": 0,
        "bbox": (x, y, x + w, y + lh * len(lines)),
        "lines": [
            {"bbox": (x, y + i * lh, x + w, y + (i + 1) * lh), "spans": [{"text": t}]}
            for i, t in enumerate(lines)
        ],
    }


def _table_row(name, value, x=20.0, y=100.0):
    return {
        "type": 0,
        "bbox": (x, y, x + 90, y + 10),
        "lines": [
            {"bbox": (x, y, x + 40, y + 10), "spans": [{"text": name}]},
            {"bbox": (x + 50, y, x + 90, y + 10), "spans": [{"text": value}]},
        ],
    }


def _page(*blocks):
    return {"blocks": list(blocks)}


def _layer(page_dict, width=1000.0, height=800.0):
    return PageTextlayer(parse_stamps(page_dict), None, width, height)


def _result(*units):
    """_result(("TOP 1", [("Vorraum", 3.0), ...]), ...)"""
    return {"units": [
        {"unit_name": u, "rooms": [
            dict({"room_name": n, "area_m2": a}, **(extra[0] if extra else {}))
            for n, a, *extra in rooms
        ]}
        for u, rooms in units
    ]}


def _areas(result):
    return {
        (u["unit_name"], r["room_name"]): r["area_m2"]
        for u in result["units"] for r in u["rooms"]
    }


def _gwm_page():
    return _page(
        _block(["TOP 1.1", "Vorraum", "B: Feinsteinz.", "F: 3,00 m2"], x=960, y=520),
        _block(["TOP 2.1", "Vorraum", "B: Feinsteinz.", "F: 2,93 m2"], x=900, y=560),
        _block(["TOP 3.1", "Vorraum", "B: Feinsteinz.", "F: 2,93 m2"], x=720, y=570),
        _block(["TOP 4.1", "Vorraum", "B: Feinsteinz.", "F: 8,64 m2"], x=610, y=520),
        _block(["TOP 2.3", "Wohnen", "B: Parkett", "F: 19,55 m2"], x=870, y=640),
        _block(["TOP 3.3", "Wohnen", "B: Parkett", "F: 19,01 m2"], x=720, y=640),
        _block(["EG.01", "Stg/Vr verglast", "B: Betonplatten", "F: 13,47 m2"], x=790, y=510),
        _block(["TOP 3", "28,07m2"], x=720, y=500),
        _block(["TOP 1", "101,73m2"], x=900, y=490),
    )


# ---------------------------------------------------------------------------
# Namen + Stempel
# ---------------------------------------------------------------------------


def test_parse_name_unit_room_number_and_words():
    p = parse_name("TOP 2.1 Vorraum")
    assert (p.unit, p.room_no, p.words) == ("u2", "2.1", frozenset({"vorraum"}))


def test_parse_name_ignores_inkl_clause():
    """"Garten TOP 1 (inkl. Terrasse, Lichthof)" ist ein Garten — sonst würde
    ein Vision-Raum "Lichthof" die Gartenfläche 160,87 bekommen."""
    assert parse_name("Garten TOP 1 (inkl. Terrasse,Lichthof)").words == {"garten"}
    assert parse_name("GARTEN ZU TOP 1 INKL. BÖSCHUNGEN WIESE").words == {"garten"}


def test_parse_name_abbreviations_but_no_synonyms():
    assert parse_name("A.fläche").words == parse_name("Abstellr.").words == {"abstellraum"}
    assert parse_name("Diele").words != parse_name("Vorraum").words
    assert parse_name("Zimmer 2").words == {"zimmer", "#2"}


def test_parse_name_unit_from_hint_and_general():
    assert parse_name("Bad", unit_hint="Top 4").unit == "u4"
    assert parse_name("W1").unit == "u1"
    assert parse_name("Stiegenhaus", unit_hint="Allgemein / Erschließung").unit == "general"
    # Einheit im Namen gewinnt gegen den Hinweis.
    assert parse_name("Lichthof TOP 1", unit_hint="Allgemein").unit == "u1"


def test_parse_stamps_block_based_name():
    stamps = parse_stamps(_page(
        _block(["STUK +2,20"], x=990, y=500),
        _block(["TOP 1.2", "Bad", "B: Feinsteinz.", "F: 4,63 m2"], x=1000, y=520),
    ))
    assert len(stamps) == 1
    s = stamps[0]
    assert (s.unit, s.room_no, s.words, s.area, s.kind) == ("u1", "1.2", {"bad"}, 4.63, "room")


def test_parse_stamps_two_stamps_in_one_block_with_perimeter_lines():
    stamps = parse_stamps(_page(_block(
        ["SCHLAFEN", "PARKETT", "11,68 m2", "14,55   m", "BAD", "FEINSTEINZEUG", "4,82 m2", "9,04   m"]
    )))
    assert [(s.words, s.area) for s in stamps] == [({"schlafen"}, 11.68), ({"bad"}, 4.82)]


def test_parse_stamps_unit_sum_is_not_a_room():
    stamps = parse_stamps(_page(_block(["TOP 3", "28,07m2"])))
    assert stamps[0].kind == "sum"
    assert not stamps[0].assignable


def test_parse_stamps_drops_table_row_with_drawing_twin_only():
    stamps = parse_stamps(_page(
        _block(["BAD", "FLIESEN", "4,76 m2"], x=700, y=300),
        _table_row("Bad", "4,76 m²", y=310),
        _table_row("Speis", "2,77 m²", y=320),     # kein Gegenstück in der Zeichnung
    ))
    assert sorted((s.raw, s.table) for s in stamps) == [("BAD", False), ("Speis", True)]


# ---------------------------------------------------------------------------
# Vertrauens-Prüfung
# ---------------------------------------------------------------------------


def _three_rooms():
    return parse_stamps(_page(
        _block(["BAD", "4,30 m2"], x=100), _block(["WC", "2,10 m2"], x=300),
        _block(["DIELE", "3,65 m2"], x=500),
    ))


@pytest.mark.parametrize("trace, coverage, expected", [
    (None, 0.0, "texttrace_unavailable"),
    ([], 0.0, "no_text"),
    ([{"type": 0, "font": "ArialMT"}, {"type": 3, "font": "ArialMT"}], 0.0, "invisible_text"),
    ([{"type": 0, "font": "GlyphLessFont"}], 0.0, "ocr_font"),
    ([{"type": 0, "font": "ArialMT", "opacity": 0.0}], 0.0, "invisible_text"),
    ([{"type": 0, "font": "ArialMT"}], 0.58, "raster_image"),
    ([{"type": 0, "font": "ArialMT"}], 0.04, None),
])
def test_assess_trust_in_doubt_scan(trace, coverage, expected):
    assert assess_trust(trace=trace, image_coverage=coverage, stamps=_three_rooms()) == expected


def test_assess_trust_needs_three_named_stamps():
    stamps = _three_rooms()[:2]
    assert assess_trust(trace=[{"type": 0, "font": "ArialMT"}], image_coverage=0.0,
                        stamps=stamps) == "too_few_stamps"


def test_untrusted_textlayer_changes_nothing():
    layer = PageTextlayer(parse_stamps(_gwm_page()), "invisible_text", 1000, 800)
    result = _result(("TOP 1", [("Vorraum", 14.7)]), ("Allgemein", [("TOP 1", 101.73)]))
    before = copy.deepcopy(result)
    report = assign_areas(result, layer)
    assert apply_report(result, report) == 0
    assert result == before
    assert {d.action for d in report.decisions} == {"unmatched"}


def test_read_page_textlayer_flags_invisible_ocr_layer():
    fitz = pytest.importorskip("fitz")
    from app.plan_analysis.textlayer_areas import read_page_textlayer

    def _doc(render_mode):
        doc = fitz.open()
        page = doc.new_page(width=600, height=400)
        for i, (name, area) in enumerate([("BAD", "4,30"), ("WC", "2,10"), ("DIELE", "3,65")]):
            page.insert_text((50 + i * 150, 50), name, fontsize=9, render_mode=render_mode)
            page.insert_text((50 + i * 150, 61), f"{area} m2", fontsize=9, render_mode=render_mode)
        return doc

    visible, invisible = _doc(0), _doc(3)
    try:
        assert read_page_textlayer(visible, 1).trust_reason is None
        assert read_page_textlayer(invisible, 1).trust_reason == "invisible_text"
    finally:
        visible.close()
        invisible.close()


# ---------------------------------------------------------------------------
# Zuordnung
# ---------------------------------------------------------------------------


def test_four_vorraeume_resolved_by_unit_in_stamp():
    result = _result(
        ("TOP 1", [("Vorraum", 14.7)]),
        ("TOP 2", [("Vorraum", None)]),
        ("TOP 3", [("Vorraum", 2.83)]),
        ("TOP 4", [("Vorraum", 13.7)]),
    )
    report = assign_areas(result, _layer(_gwm_page()))
    apply_report(result, report)
    assert _areas(result) == {
        ("TOP 1", "Vorraum"): 3.00, ("TOP 2", "Vorraum"): 2.93,
        ("TOP 3", "Vorraum"): 2.93, ("TOP 4", "Vorraum"): 8.64,
    }
    # Gespiegelte Wohnungen mit gleichem Wert (2,93) sind KEIN Konflikt.
    assert report.count("conflict") == 0


def test_no_fallback_to_weaker_stamp_when_own_unit_taken():
    """Zwei "TOP 2 / Vorraum": der eigene Stempel ist umstritten — keiner der
    beiden darf auf den einheitenlosen Stiegenhaus-Vorraum (13,47) ausweichen."""
    result = _result(("TOP 2", [("Vorraum", 2.5), ("Vorraum", 6.41)]))
    report = assign_areas(result, _layer(_gwm_page()))
    apply_report(result, report)
    assert [r["area_m2"] for r in result["units"][0]["rooms"]] == [2.5, 6.41]


def test_merged_vision_room_does_not_take_part_stamp():
    """Echter Fall (GWM): Vision legt "A Fläche / Wohnen" an und erfindet
    Raumnummern ("TOP 3.3" für den Mischraum, "TOP 3.5" für Wohnen). Der
    Wohnen-Stempel gehört dem echten "Wohnen"; der Mischraum bleibt ohne Wert."""
    page = _page(
        _block(["TOP 3.3", "Wohnen", "B: Parkett", "F: 19,01 m2"], x=720, y=640),
        _block(["TOP 3.4", "A.fläche", "B: Parkett", "F: 2,00 m2"], x=690, y=610),
    )
    result = _result(("TOP 3", [
        ("A Fläche / Wohnen", None, {"room_number": "TOP 3.3"}),
        ("Wohnen", None, {"room_number": "TOP 3.5"}),
    ]))
    report = assign_areas(result, _layer(page))
    apply_report(result, report)
    assert [r["area_m2"] for r in result["units"][0]["rooms"]] == [None, 19.01]


def test_abbreviated_vision_name_matches_longer_stamp():
    result = _result(("Allgemein", [("Stiegenhaus mit Profilverglasung", 17.18)]))
    apply_report(result, assign_areas(result, _layer(_gwm_page())))
    assert result["units"][0]["rooms"][0]["area_m2"] == 13.47


def test_contested_stamp_goes_to_exact_reader():
    result = _result(("TOP 2", [("Vorraum", 2.93), ("Vorraum", 6.41)]))
    report = assign_areas(result, _layer(_gwm_page()))
    assert [d.action for d in report.decisions] == ["confirm", "unmatched"]


def test_duplicates_without_unit_by_value_then_elimination():
    page = _page(
        _block(["BAD", "FEINSTEINZEUG", "4,30 m2"], x=1040, y=870),
        _block(["BAD", "FEINSTEINZEUG", "4,82 m2"], x=1350, y=880),
        _block(["DIELE", "3,65 m2"], x=1020, y=760),
    )
    result = _result(("W1", [("BAD", 4.8)]), ("W2", [("BAD", None)]))
    report = assign_areas(result, _layer(page, 2600, 1480))
    apply_report(result, report)
    assert _areas(result) == {("W1", "BAD"): 4.82, ("W2", "BAD"): 4.30}
    assert [d.step for d in report.decisions] == ["value", "elimination"]


def test_value_step_refuses_unclear_reading():
    page = _page(_block(["BAD", "4,30 m2"], x=100), _block(["BAD", "4,82 m2"], x=900))
    result = _result(("W1", [("BAD", 4.55)]), ("W2", [("BAD", 4.6)]))
    report = assign_areas(result, _layer(page))
    assert {d.action for d in report.decisions} == {"unmatched"}


def test_same_value_duplicates_are_both_filled():
    page = _page(_block(["AR", "2,00 m2"], x=1160), _block(["AR", "2,00 m2"], x=1250))
    result = _result(("W1", [("AR", None)]), ("W2", [("AR", 7.0)]))
    report = assign_areas(result, _layer(page, 2600, 1480))
    apply_report(result, report)
    assert _areas(result) == {("W1", "AR"): 2.0, ("W2", "AR"): 2.0}


def test_position_step_uses_pin_in_pdf_points():
    page = _page(
        _block(["BAD", "4,30 m2"], x=100, y=100, w=40),   # Mittelpunkt ≈ (120, 110)
        _block(["BAD", "4,82 m2"], x=700, y=500, w=40),   # Mittelpunkt ≈ (720, 510)
    )
    # Crop (0,0)-(1000,800) Punkte, Bild 500×400 px → 1 px = 2 pt.
    frame = {"x0": 0, "y0": 0, "x1": 1000, "y1": 800, "image_w": 500, "image_h": 400}
    result = _result(
        ("W1", [("BAD", None, {"position_x": 360, "position_y": 255})]),
        ("W2", [("BAD", None, {"position_x": 60, "position_y": 55})]),
    )
    report = assign_areas(result, _layer(page), frame=frame)
    apply_report(result, report)
    assert _areas(result) == {("W1", "BAD"): 4.82, ("W2", "BAD"): 4.30}
    assert report.pins_usable == 2


def test_cautious_policy_blocks_swapped_values_trust_unit_writes():
    """Echter Fall (GWM, Lauf 4): TOP 2 las 19,01 = exakt TOP 3.3, TOP 3 las
    18,55. Vorsichtig: beide bleiben. Einheit vertrauen: beide korrigiert."""
    base = _result(("TOP 2", [("Wohnen", 19.01)]), ("TOP 3", [("Wohnen", 18.55)]))

    cautious = copy.deepcopy(base)
    report = assign_areas(cautious, _layer(_gwm_page()), policy="cautious")
    apply_report(cautious, report)
    assert report.count("conflict") == 2
    assert _areas(cautious) == _areas(base)

    trusting = copy.deepcopy(base)
    apply_report(trusting, assign_areas(trusting, _layer(_gwm_page()), policy="trust_unit"))
    assert _areas(trusting) == {("TOP 2", "Wohnen"): 19.55, ("TOP 3", "Wohnen"): 19.01}


def test_cautious_policy_protects_correct_value_under_wrong_unit():
    """Vision ordnet den Wohnraum von TOP 2 (19,55 exakt gelesen) fälschlich
    TOP 3 zu. Die Einheit allein würde 19,01 darüberschreiben."""
    result = _result(("TOP 3", [("Wohnen", 19.55)]))
    report = assign_areas(result, _layer(_gwm_page()))
    apply_report(result, report)
    assert report.decisions[0].action == "conflict"
    assert _areas(result) == {("TOP 3", "Wohnen"): 19.55}


def test_unit_sum_as_room_area_is_cleared_room_kept():
    result = _result(("Stiegenhaus / Allgemein", [("TOP 1", 101.73), ("Stg/Vr verglast", 28.07)]))
    report = assign_areas(result, _layer(_gwm_page()))
    apply_report(result, report)
    rooms = result["units"][0]["rooms"]
    assert len(rooms) == 2
    assert rooms[0]["area_m2"] is None                     # Summe → leer
    assert rooms[1]["area_m2"] == 13.47                    # Summe, aber Stempel passt → korrigiert
    assert [d.action for d in report.decisions] == ["sum_cleared", "correct"]


def test_rooms_never_added_or_removed_and_orphans_logged():
    result = _result(("TOP 1", [("Vorraum", None), ("Küche", 9.5), ("Raum", None)]))
    before = sum(len(u["rooms"]) for u in result["units"])
    report = assign_areas(result, _layer(_gwm_page()))
    apply_report(result, report)
    assert sum(len(u["rooms"]) for u in result["units"]) == before
    assert len(report.decisions) == before
    assert any("TOP 4.1 Vorraum" in o for o in report.orphan_stamps)
    assert result["units"][0]["rooms"][1]["area_m2"] == 9.5   # ohne Stempel: Vision-Wert bleibt


def test_unknown_policy_rejected():
    with pytest.raises(ValueError):
        assign_areas(_result(), _layer(_gwm_page()), policy="yolo")


# ---------------------------------------------------------------------------
# Pipeline-Anbindung
# ---------------------------------------------------------------------------


def test_full_page_image_frame_mirrors_vision_cap():
    import io

    from PIL import Image

    from app.plan_analysis.pipeline import _full_page_image_frame

    buf = io.BytesIO()
    Image.new("RGB", (4000, 2000), (255, 255, 255)).save(buf, format="PNG")
    frame = _full_page_image_frame(buf.getvalue(), 2211.0, 1360.0)
    assert frame == {"x0": 0.0, "y0": 0.0, "x1": 2211.0, "y1": 1360.0,
                     "image_w": 1536, "image_h": 768}
    assert _full_page_image_frame(b"not an image", 10.0, 10.0) is None


def test_textlayer_areas_mode_defaults_off():
    from app.config import Settings

    assert Settings.model_fields["textlayer_areas_mode"].default == "off"
