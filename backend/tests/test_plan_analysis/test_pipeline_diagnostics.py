"""Tests for the v23.1.1 hotfix changes.

Two pieces:

1. ``_VISION_MAX_TOKENS`` is the new module constant for the
   per-page Anthropic ``messages.create`` call's output cap. Bumped
   from 4096 to 8192 in v23.1.1 because larger plans (130+ rooms)
   started hitting BadRequestError once the v23.1 pin-coordinate
   fields added ~30 tokens per room. Locked in a test so a future
   refactor doesn't silently revert it.

2. ``_format_page_error`` is the helper that turns a Vision-call
   exception into the user-facing string in the per-page error
   list. v23.1 surfaced only ``type(e).__name__`` ("BadRequestError")
   which is useless for diagnosis — the hotfix appends the
   truncated ``str(e)`` so the operator can read what Anthropic
   actually said.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _VISION_MAX_TOKENS
# ---------------------------------------------------------------------------


def test_vision_max_tokens_is_8192_after_hotfix():
    """v23.1.1 doubled the cap from 4096 → 8192 because larger
    plans were tripping max_tokens-related 400 errors. The
    explicit assertion locks the new value so it can't slide back
    on a refactor."""
    from app.plan_analysis.pipeline import _VISION_MAX_TOKENS

    assert _VISION_MAX_TOKENS == 8192


def test_vision_max_tokens_is_used_in_extract_call():
    """Defence in depth: assert the constant is actually wired into
    the messages.create call, not just declared. We grep the source
    rather than mock-and-patch the Anthropic SDK because the SDK
    surface is large and the source-grep is faithful to whatever a
    future refactor might do — as long as the constant is
    referenced, we accept the wiring."""
    import inspect

    from app.plan_analysis import pipeline

    source = inspect.getsource(pipeline._extract_rooms_from_image)
    assert "_VISION_MAX_TOKENS" in source, (
        "_extract_rooms_from_image must reference _VISION_MAX_TOKENS "
        "so the bumped output cap actually takes effect on live calls."
    )


# ---------------------------------------------------------------------------
# _format_page_error
# ---------------------------------------------------------------------------


def test_format_page_error_includes_class_name_and_message():
    """The whole point of v23.1.1 part A: when Anthropic returns a
    400, the user-visible banner must include the underlying
    message text, not just the exception type. Otherwise we're
    debugging blind."""
    from app.plan_analysis.pipeline import _format_page_error

    class BadRequestError(Exception):
        pass

    exc = BadRequestError("max_tokens insufficient for expected output")
    out = _format_page_error(3, exc)

    assert "Seite 3" in out
    assert "BadRequestError" in out
    assert "max_tokens insufficient for expected output" in out


def test_format_page_error_truncates_long_messages():
    """Anthropic occasionally returns multi-kilobyte error bodies
    (full JSON of the rejected request). Truncate so the surrounding
    error banner doesn't scroll a user off the page."""
    from app.plan_analysis.pipeline import _format_page_error

    long_body = "x" * 1000
    out = _format_page_error(1, ValueError(long_body), max_chars=100)

    # 100 char body cap + the prefix overhead. Allow some slack for
    # the literal "Seite 1: ValueError — " prefix.
    assert len(out) < 200
    assert out.startswith("Seite 1: ValueError")
    # First 100 chars of the body must be in the output.
    assert "x" * 100 in out


def test_format_page_error_handles_empty_message():
    """``str(exc)`` is sometimes empty (raise SomeError() with no
    args). The function must not produce a dangling em-dash or
    trailing whitespace in that case — bare exception class is
    enough."""
    from app.plan_analysis.pipeline import _format_page_error

    out = _format_page_error(2, RuntimeError())

    assert out == "Seite 2: RuntimeError"
    assert "—" not in out
    assert not out.endswith(" ")


# ---------------------------------------------------------------------------
# v24.4.5 — Room-Count-Helper + notes-Durchreichung
# ---------------------------------------------------------------------------
#
# Hintergrund: cluttered-Pläne (voll mit Möbeln/Maßketten) lieferten
# manchmal valides JSON mit ``units: []`` zurück — 0 Räume, aber kein
# Fehler. Dieser Fall war im Log ein blinder Fleck. v24.4.5:
#
#   * ``_count_extracted_rooms`` ist die pure Zähl-Logik, die das
#     0-Räume-Diagnose-Logging speist. Hier direkt getestet, ohne den
#     Anthropic-SDK zu mocken.
#   * Das KI-``notes``-Feld (im Prompt nur für den 0-Räume-Fall) muss
#     den Parse-Pfad unverändert überstehen, damit es im Log und in
#     der User-Fehlermeldung landen kann. Geprüft über die echte
#     Parse-Kette ``_extract_json_blob`` → ``json.loads``.


def test_count_extracted_rooms_zero_for_empty_units():
    """Der Kernfall: valides JSON, aber leere units → 0. Genau das
    triggert das ``vision.zero_rooms``-Warning + die KI-notes-Meldung."""
    from app.plan_analysis.pipeline import _count_extracted_rooms

    assert _count_extracted_rooms({"floor_name": "EG", "units": []}) == 0


def test_count_extracted_rooms_counts_across_units():
    """Räume werden über mehrere Einheiten hinweg aufsummiert."""
    from app.plan_analysis.pipeline import _count_extracted_rooms

    parsed = {
        "units": [
            {"unit_name": "W1", "rooms": [{"room_name": "Bad"}, {"room_name": "WC"}]},
            {"unit_name": "W2", "rooms": [{"room_name": "Wohnen"}]},
        ]
    }
    assert _count_extracted_rooms(parsed) == 3


def test_count_extracted_rooms_tolerates_malformed_shapes():
    """Der Helper speist nur ein Log-Line — er darf NIE werfen, sonst
    würde aus einer 0-Räume-Diagnose ein harter Pipeline-Absturz.
    Alle kaputten Formen kollabieren auf 0."""
    from app.plan_analysis.pipeline import _count_extracted_rooms

    assert _count_extracted_rooms(None) == 0
    assert _count_extracted_rooms("nonsense") == 0
    assert _count_extracted_rooms({}) == 0
    assert _count_extracted_rooms({"units": None}) == 0
    assert _count_extracted_rooms({"units": "x"}) == 0
    # Einheit ohne rooms-Liste / mit null rooms.
    assert _count_extracted_rooms({"units": [{"unit_name": "W1"}]}) == 0
    assert _count_extracted_rooms({"units": [{"rooms": None}]}) == 0
    # Gemischt: eine kaputte + eine gültige Einheit → nur die gültige zählt.
    assert (
        _count_extracted_rooms(
            {"units": ["garbage", {"rooms": [{"room_name": "Bad"}]}]}
        )
        == 1
    )


def test_notes_field_survives_parse_chain_fenced():
    """notes-Durchreichung: ein ```json-gefenctes Vision-Resultat mit
    ``notes`` muss nach ``_extract_json_blob`` + ``json.loads`` das
    notes-Feld unverändert tragen — sonst käme der Grund nie ins Log
    oder in die User-Meldung."""
    import json

    from app.plan_analysis.pipeline import _count_extracted_rooms, _extract_json_blob

    raw = (
        "```json\n"
        '{"floor_name": "EG", "notes": "Wirkt wie ein Lageplan, kein Grundriss.", '
        '"units": []}\n'
        "```"
    )
    parsed = json.loads(_extract_json_blob(raw))
    assert parsed["notes"] == "Wirkt wie ein Lageplan, kein Grundriss."
    assert _count_extracted_rooms(parsed) == 0


def test_notes_absent_in_normal_result():
    """Findet die KI Räume, lässt sie ``notes`` weg — der Parse-Pfad
    darf das nicht erzwingen. ``.get('notes')`` ist dann schlicht None,
    und der Room-Count ist > 0 (kein zero_rooms-Warning)."""
    import json

    from app.plan_analysis.pipeline import _count_extracted_rooms, _extract_json_blob

    raw = (
        '{"floor_name": "EG", "units": [{"unit_name": "W1", '
        '"rooms": [{"room_name": "WOHNEN / KOCHEN"}]}]}'
    )
    parsed = json.loads(_extract_json_blob(raw))
    assert parsed.get("notes") is None
    assert _count_extracted_rooms(parsed) == 1
