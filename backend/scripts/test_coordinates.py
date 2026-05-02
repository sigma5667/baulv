"""Phase-1 smoke test for the v23.1 plan-pin coordinate extraction.

Drives the same Vision pipeline a production plan-analysis request
goes through against any test PDF, then prints — and writes to a
markdown report — what coordinates Vision returned for each room.

Goal of this test
=================

Decide whether Vision can reliably produce coordinates we'd later
render as pins on the plan in Phase 2. Pass criterion: at least
**70 %** of extracted rooms have all four coordinate fields
(position_x, position_y, bbox_width, bbox_height) populated. Below
that, Vision isn't carrying its weight and the prompt needs more
work before we build the frontend on top.

What the script does NOT check
==============================

* That coordinates are *correct* in absolute terms — that needs a
  side-by-side visual comparison against the rendered plan, which
  is a manual step. The script flags suspiciously-out-of-bounds
  numbers (negative, > 10 000) so we catch obvious nonsense.
* Whether Phase 2 pin rendering looks good — the user says Phase 2
  is a separate change. This script is the data-quality gate.

Usage
=====

::

    cd backend
    export ANTHROPIC_API_KEY=sk-ant-...

    python scripts/test_coordinates.py
    python scripts/test_coordinates.py path/to/other-plan.pdf
    python scripts/test_coordinates.py plan.pdf --report custom.md

Default PDF path is ``../test-plaene/plan_test.pdf.pdf``. The
``--report`` flag lets the operator choose where to write the
markdown summary.

Exit codes
==========

* 0 — extraction succeeded, ≥ 70 % of rooms have full coordinates.
* 1 — extraction failed (network, parse, or auth).
* 2 — extraction succeeded but < 70 % of rooms had coordinates.
  CI / decision gate should treat this as "Phase 2 not unblocked".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Make ``app.*`` importable when running from ``backend/``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.plan_analysis.pipeline import (  # noqa: E402
    _extract_rooms_from_image,
    _pdf_to_images,
)


# Pass criterion from the spec.
COORD_COVERAGE_THRESHOLD = 0.70


# Crude sanity bound — Vision occasionally returns coordinate values
# in the millions when it's lost (treats text from a chart legend as
# a coordinate, etc.). 10 000 px is generous: a PDF page rendered at
# 300 DPI is roughly 2500 × 3500 px for A3. Anything beyond 10 k is
# almost certainly garbage.
COORD_SANITY_MAX = 10_000


# ---------------------------------------------------------------------------
# Mini ANSI helpers — same vocabulary as the other scripts in this dir
# ---------------------------------------------------------------------------


class _C:
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD = "\033[1m"
    END = "\033[0m"


def _section(title: str) -> None:
    print(f"\n{_C.BOLD}{_C.BLUE}── {title} ──{_C.END}")


def _ok(text: str) -> None:
    print(f"{_C.GREEN}✓{_C.END} {text}")


def _warn(text: str) -> None:
    print(f"{_C.YELLOW}⚠{_C.END} {text}")


def _err(text: str) -> None:
    print(f"{_C.RED}✗{_C.END} {text}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Coordinate validation + report rendering
# ---------------------------------------------------------------------------


def _is_full_coord(room: dict) -> bool:
    """True iff all four coordinate fields are populated AND inside
    a sane numeric range. Mirrors the pipeline's own validation —
    positive integers only — plus the upper-bound sanity check
    described in the module docstring."""
    for key in ("position_x", "position_y", "bbox_width", "bbox_height"):
        v = room.get(key)
        if v is None:
            return False
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return False
        if iv <= 0 or iv > COORD_SANITY_MAX:
            return False
    return True


def _flatten_rooms(parsed: dict) -> list[dict]:
    rooms: list[dict] = []
    for unit in parsed.get("units", []) or []:
        for room in unit.get("rooms", []) or []:
            rooms.append(room)
    return rooms


def _render_report(
    pdf_path: Path,
    pages_results: list[tuple[int, dict]],
) -> tuple[str, int, int]:
    """Build the markdown report and return ``(text, n_with, n_total)``."""
    lines: list[str] = []
    lines.append(f"# Plan-Pin-Koordinaten Test-Lauf — {pdf_path.name}")
    lines.append("")
    lines.append(f"- **Plan**: `{pdf_path}`")
    lines.append(f"- **Seiten analysiert**: {len(pages_results)}")
    lines.append("")

    # Summary section comes first (the operator might not scroll).
    n_total = sum(len(_flatten_rooms(r)) for _, r in pages_results)
    n_with = sum(
        1
        for _, r in pages_results
        for room in _flatten_rooms(r)
        if _is_full_coord(room)
    )
    coverage = (n_with / n_total) if n_total else 0.0

    lines.append("## Zusammenfassung")
    lines.append("")
    lines.append(f"- **Räume insgesamt**: {n_total}")
    lines.append(
        f"- **Räume mit allen 4 Koordinaten-Feldern**: "
        f"{n_with} ({coverage:.0%})"
    )
    lines.append(
        f"- **Schwellwert für Phase-2-Freigabe**: "
        f"≥ {COORD_COVERAGE_THRESHOLD:.0%}"
    )
    if coverage >= COORD_COVERAGE_THRESHOLD:
        lines.append(
            f"- **Status**: ✅ erreicht — Phase 2 (Frontend-Pins) kann "
            f"starten."
        )
    else:
        lines.append(
            f"- **Status**: ❌ nicht erreicht — Prompt-Iteration nötig. "
            f"Prüfe ob die Pin-Sektion im Prompt klar genug ist und "
            f"ob Vision die Bildmasse richtig einschätzt."
        )
    lines.append("")

    # Per-page detail table
    for page_number, page_result in pages_results:
        lines.append(f"## Seite {page_number}")
        lines.append("")
        rooms = _flatten_rooms(page_result)
        if not rooms:
            lines.append("_(keine Räume erkannt)_")
            lines.append("")
            continue

        lines.append(
            "| Raum | x | y | bbox W×H | Status |"
        )
        lines.append("|--|--:|--:|--|--|")
        for room in rooms:
            name = room.get("room_name") or "?"
            x = room.get("position_x")
            y = room.get("position_y")
            w = room.get("bbox_width")
            h = room.get("bbox_height")
            full = _is_full_coord(room)
            status = "✓ vollständig" if full else "○ unvollständig"
            bbox = f"{w}×{h}" if w and h else "—"
            lines.append(
                f"| {name} | {x if x is not None else '—'} "
                f"| {y if y is not None else '—'} "
                f"| {bbox} | {status} |"
            )
        lines.append("")

    # Raw payload appendix
    lines.append("## Rohdaten (Vision-JSON je Seite)")
    lines.append("")
    for page_number, page_result in pages_results:
        lines.append(f"### Seite {page_number}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(page_result, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    return "\n".join(lines), n_with, n_total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


DEFAULT_TEST_PDF = (
    Path(__file__).resolve().parent.parent.parent
    / "test-plaene"
    / "plan_test.pdf.pdf"
)


async def run(pdf_path: Path, report_path: Path) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _err(
            "ANTHROPIC_API_KEY ist nicht gesetzt. Den Backend-Key "
            "exportieren bevor das Script läuft."
        )
        return 1
    if not pdf_path.exists():
        _err(f"PDF nicht gefunden: {pdf_path}")
        return 1

    _section(f"PDF rendern: {pdf_path.name}")
    images = _pdf_to_images(str(pdf_path))
    _ok(
        f"{len(images)} Seite(n) gerendert "
        f"(adaptive DPI/JPEG je nach Größe — siehe Backend-Logs)."
    )

    _section("Vision-Calls absetzen")
    pages_results: list[tuple[int, dict]] = []
    for i, (image_bytes, mime_type) in enumerate(images, start=1):
        print(f"  → Seite {i}/{len(images)} ({mime_type}, {len(image_bytes):,} Bytes)…")
        try:
            parsed = await _extract_rooms_from_image(
                image_bytes, i, mime_type=mime_type
            )
        except Exception as exc:  # noqa: BLE001
            _err(f"Vision-Call für Seite {i} gescheitert: {exc}")
            return 1
        if parsed is None:
            _warn(f"Seite {i}: Vision-Antwort nicht parsebar — übersprungen.")
            continue
        pages_results.append((i, parsed))

    if not pages_results:
        _err("Keine Vision-Antwort konnte geparst werden.")
        return 1

    md, n_with, n_total = _render_report(pdf_path, pages_results)
    report_path.write_text(md, encoding="utf-8")
    _ok(f"Bericht geschrieben: {report_path}")

    coverage = n_with / n_total if n_total else 0.0
    _section("Zusammenfassung")
    print(f"  Räume insgesamt:               {n_total}")
    print(f"  Räume mit allen 4 Koordinaten: {n_with} ({coverage:.0%})")
    print(f"  Schwellwert:                   {COORD_COVERAGE_THRESHOLD:.0%}")

    if coverage >= COORD_COVERAGE_THRESHOLD:
        _ok("Schwellwert erreicht — Phase 2 (Frontend-Pins) kann starten.")
        return 0
    _warn(
        "Schwellwert NICHT erreicht — Prompt-Iteration nötig, bevor "
        "Phase 2 sinnvoll ist."
    )
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdf",
        nargs="?",
        type=Path,
        default=DEFAULT_TEST_PDF,
        help=(
            "PDF-Pfad. Default: test-plaene/plan_test.pdf.pdf "
            "(siehe docs/PROMPT_TEST_v22_3.md für die Konvention)."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/COORDINATE_TEST_RESULT.md"),
        help="Markdown-Report-Ziel (default: docs/COORDINATE_TEST_RESULT.md)",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(run(args.pdf, args.report))
    except KeyboardInterrupt:
        _warn("\nAbgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
