"""End-to-end smoke test for the Vision room-extraction prompt.

Runs the same pipeline a production plan-analysis request goes
through (PDF → PNG → Claude Vision → JSON) against a single test
PDF, then compares the result row-by-row against an embedded
ground-truth table the architect printed on the plan.

Why this lives in-tree
======================

* The Vision prompt is the single biggest "smart" component in the
  whole stack. Drift in Anthropic's model behaviour, prompt
  regressions, or accidental file-encoding changes can silently
  cut extraction quality in half — and you'd only notice when a
  beta tester complains. A standalone runnable script means we can
  verify the prompt *immediately* after every change and eyeball
  the diff between prompt iterations.
* Avoids touching the production pipeline. Reuses the same
  ``_pdf_to_images`` and ``_extract_rooms_from_image`` helpers so
  the test stays representative.

Usage
=====

::

    cd backend
    export ANTHROPIC_API_KEY=sk-ant-...

    # Default: the "Kleimayrngasse 3 EG" plan we shipped a v22.3
    # ground-truth table for.
    python scripts/test_vision_prompt.py

    # Other plans:
    python scripts/test_vision_prompt.py /path/to/other-plan.pdf
    python scripts/test_vision_prompt.py /path/to/plan.pdf \\
        --report report.md

Exit codes
==========

* 0 — extraction succeeded; report written.
* 1 — extraction failed (network, parse, or auth).
* 2 — extraction succeeded but at least one ``labeled``-source
  perimeter is more than 5 % off the ground-truth value. The CI
  hook should fail builds on this.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Make the ``app`` package importable when the script runs from the
# ``backend`` directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.plan_analysis.pipeline import (  # noqa: E402
    _extract_rooms_from_image,
    _pdf_to_images,
)


# ---------------------------------------------------------------------------
# Ground truth — the values printed on the test plan by the
# architect's CAD output. Keyed by PDF filename so we can extend
# the table later without touching the script logic.
# ---------------------------------------------------------------------------


# Each entry: (room_name_substring, area_m2, perimeter_m).
# Names are matched case-insensitively as substrings — Vision sometimes
# normalises whitespace differently than the plan does.
GROUND_TRUTH: dict[str, list[tuple[str, float, float]]] = {
    "plan_test.pdf.pdf": [
        # Wohnung W1 (rechts, ground-truth from the plan inline labels)
        ("WOHNEN / KOCHEN", 32.84, 24.32),
        ("SCHLAFEN", 12.03, 14.72),
        ("BAD", 4.30, 8.51),
        ("DIELE", 3.65, 8.09),
        ("AR", 2.00, 5.70),
        # Wohnung W2 (links)
        ("WOHNEN / KOCHEN", 32.66, 25.07),
        ("SCHLAFEN", 11.68, 14.55),
        ("BAD", 4.82, 9.04),
        ("DIELE", 3.85, 8.37),
        ("AR", 2.00, 5.70),
        # Erschliessungs-Räume
        ("EINGANG", 2.52, 6.35),
    ],
}


# Acceptable delta between Vision's ``labeled``-source perimeter and
# the ground-truth value, expressed as a fraction of the truth. 5 %
# matches the user's spec — beyond that we flag the run as failing.
LABELED_TOLERANCE = 0.05


# ---------------------------------------------------------------------------
# Mini ANSI helpers — same vocabulary as the demo script
# ---------------------------------------------------------------------------


class _C:
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"
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
# Comparison logic
# ---------------------------------------------------------------------------


def _flatten_extracted_rooms(parsed: dict) -> list[dict]:
    """Walk the nested units→rooms tree from one Vision response and
    return a flat list of room dicts, in extraction order."""
    rooms: list[dict] = []
    for unit in parsed.get("units", []) or []:
        for room in unit.get("rooms", []) or []:
            rooms.append(room)
    return rooms


def _match_ground_truth(
    extracted: list[dict], truth: list[tuple[str, float, float]]
) -> list[dict]:
    """Pair each ground-truth row with the closest extracted room.

    Strategy: take the truth rows in order, find the first extracted
    room that matches the name substring (case-insensitive) AND
    hasn't already been matched. This is robust to Vision returning
    the rooms in a different order than the plan, and to it splitting
    "WOHNEN / KOCHEN" into different whitespace.
    """
    matched_indices: set[int] = set()
    pairs: list[dict] = []
    for truth_name, truth_area, truth_perimeter in truth:
        best_idx = None
        for i, room in enumerate(extracted):
            if i in matched_indices:
                continue
            extracted_name = (room.get("room_name") or "").upper()
            if truth_name.upper() in extracted_name:
                best_idx = i
                break
        if best_idx is None:
            pairs.append(
                {
                    "truth_name": truth_name,
                    "truth_area": truth_area,
                    "truth_perimeter": truth_perimeter,
                    "extracted": None,
                }
            )
            continue
        matched_indices.add(best_idx)
        pairs.append(
            {
                "truth_name": truth_name,
                "truth_area": truth_area,
                "truth_perimeter": truth_perimeter,
                "extracted": extracted[best_idx],
            }
        )
    return pairs


def _delta_pct(extracted: float | None, truth: float) -> float | None:
    """Signed percentage delta of extracted vs truth. ``None`` if
    the extracted value is missing — the caller decides how to count
    that case (we count it as a miss but not a failure)."""
    if extracted is None:
        return None
    if truth == 0:
        return None
    return (float(extracted) - truth) / truth * 100.0


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _render_markdown_report(
    *,
    pdf_path: Path,
    pages_analysed: int,
    pairs: list[dict],
    raw_results: list[dict],
) -> str:
    lines: list[str] = []
    lines.append(f"# Vision-Prompt Test-Lauf — {pdf_path.name}")
    lines.append("")
    lines.append(f"- **Plan**: `{pdf_path}`")
    lines.append(f"- **Seiten analysiert**: {pages_analysed}")
    lines.append(
        f"- **Räume erwartet (Ground Truth)**: {len(pairs)}"
    )
    matched = sum(1 for p in pairs if p["extracted"] is not None)
    lines.append(f"- **Räume gematcht**: {matched} / {len(pairs)}")
    lines.append("")

    # Per-room comparison table
    lines.append("## Vergleichstabelle")
    lines.append("")
    lines.append(
        "| Raum (Plan) | Fläche-Plan | Fläche-KI | Δ % | "
        "Umfang-Plan | Umfang-KI | Δ % | Source | Status |"
    )
    lines.append(
        "|--|--:|--:|--:|--:|--:|--:|--|--|"
    )

    n_labeled_within_tolerance = 0
    n_labeled_total = 0
    n_labeled_breaches: list[str] = []

    def _fmt_num(value: float | None, suffix: str) -> str:
        return f"{value:.2f} {suffix}" if value is not None else "—"

    def _fmt_delta(delta: float | None) -> str:
        return f"{delta:+.1f} %" if delta is not None else "—"

    for pair in pairs:
        truth_name = pair["truth_name"]
        truth_area = pair["truth_area"]
        truth_peri = pair["truth_perimeter"]
        extracted = pair["extracted"]

        if extracted is None:
            lines.append(
                f"| {truth_name} | {truth_area:.2f} m² | — | — | "
                f"{truth_peri:.2f} m | — | — | — | ❌ kein Match |"
            )
            continue

        ext_area = extracted.get("area_m2")
        ext_peri = extracted.get("perimeter_m")
        ext_source = extracted.get("perimeter_source") or "—"

        area_delta = _delta_pct(ext_area, truth_area)
        peri_delta = _delta_pct(ext_peri, truth_peri)

        # Status: ✓ if perimeter is within tolerance for labeled
        # rows, ⚠ if soft miss, — if no Vision perimeter at all.
        status = "—"
        if ext_peri is None:
            status = "⚠ Umfang fehlt"
        elif ext_source == "labeled":
            n_labeled_total += 1
            assert peri_delta is not None
            if abs(peri_delta) <= LABELED_TOLERANCE * 100:
                n_labeled_within_tolerance += 1
                status = "✓"
            else:
                status = f"❌ {peri_delta:+.1f} %"
                n_labeled_breaches.append(
                    f"{truth_name}: KI {ext_peri} m vs. Plan {truth_peri} m"
                )
        elif ext_source == "computed":
            assert peri_delta is not None
            status = (
                "✓ (computed)"
                if abs(peri_delta) <= 10
                else f"⚠ {peri_delta:+.1f} %"
            )
        elif ext_source in ("estimated", "vision"):
            status = "○ Schätzung/Legacy"

        room_name = extracted.get("room_name") or truth_name
        lines.append(
            "| "
            + room_name
            + f" | {truth_area:.2f} m²"
            + f" | {_fmt_num(ext_area, 'm²')}"
            + f" | {_fmt_delta(area_delta)}"
            + f" | {truth_peri:.2f} m"
            + f" | {_fmt_num(ext_peri, 'm')}"
            + f" | {_fmt_delta(peri_delta)}"
            + f" | {ext_source}"
            + f" | {status} |"
        )

    lines.append("")
    lines.append("## Zusammenfassung")
    lines.append("")
    lines.append(
        f"- **Labeled-Source-Räume**: {n_labeled_total} insgesamt, "
        f"davon {n_labeled_within_tolerance} innerhalb 5 %-Toleranz."
    )
    if n_labeled_breaches:
        lines.append(
            f"- **Toleranz-Verletzungen** ({len(n_labeled_breaches)}):"
        )
        for breach in n_labeled_breaches:
            lines.append(f"  - {breach}")
    else:
        lines.append("- **Keine Toleranz-Verletzungen.** ✓")
    lines.append("")

    # Raw payload — appendix
    lines.append("## Rohdaten (Vision-JSON je Seite)")
    lines.append("")
    for i, page in enumerate(raw_results, start=1):
        lines.append(f"### Seite {i}")
        lines.append("")
        lines.append("```json")
        import json

        lines.append(
            json.dumps(page, indent=2, ensure_ascii=False)
        )
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


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
    raw_results: list[dict] = []
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
            _warn(
                f"Seite {i}: Vision-Antwort nicht als JSON parsebar — "
                "übersprungen."
            )
            continue
        raw_results.append(parsed)
    if not raw_results:
        _err("Keine Vision-Antwort konnte geparst werden.")
        return 1

    # Flatten across all pages.
    extracted: list[dict] = []
    for page in raw_results:
        extracted.extend(_flatten_extracted_rooms(page))
    _ok(f"{len(extracted)} Räume insgesamt extrahiert.")

    truth = GROUND_TRUTH.get(pdf_path.name)
    if truth is None:
        _warn(
            "Keine Ground Truth für diese PDF — Bericht zeigt nur "
            "die Rohdaten ohne Vergleich."
        )
        truth = []

    pairs = _match_ground_truth(extracted, truth) if truth else []

    md = _render_markdown_report(
        pdf_path=pdf_path,
        pages_analysed=len(images),
        pairs=pairs,
        raw_results=raw_results,
    )
    report_path.write_text(md, encoding="utf-8")
    _ok(f"Bericht geschrieben: {report_path}")

    # Exit status: tolerance breaches → 2.
    n_breaches = sum(
        1
        for p in pairs
        if (
            p["extracted"] is not None
            and p["extracted"].get("perimeter_source") == "labeled"
            and p["extracted"].get("perimeter_m") is not None
            and abs(
                (
                    float(p["extracted"]["perimeter_m"])
                    - p["truth_perimeter"]
                )
                / p["truth_perimeter"]
            )
            > LABELED_TOLERANCE
        )
    )
    if n_breaches > 0:
        _warn(
            f"{n_breaches} labeled-Source-Räume außerhalb der "
            f"5 %-Toleranz — Prompt prüfen."
        )
        return 2
    _ok("Alle labeled-Source-Räume innerhalb 5 %-Toleranz.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdf",
        nargs="?",
        type=Path,
        default=DEFAULT_TEST_PDF,
        help="PDF-Pfad (default: test-plaene/plan_test.pdf.pdf)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/PROMPT_TEST_RESULT.md"),
        help="Ziel für den Markdown-Bericht (default: docs/PROMPT_TEST_RESULT.md)",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(run(args.pdf, args.report))
    except KeyboardInterrupt:
        _warn("\nAbgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
