"""Standalone end-to-end runner für den v24.4.6 Two-Pass-Plan-Analyse-Flow.

Was er macht
============

Genau dasselbe wie ``analyze_plan`` für eine echte Plan-Analyse, aber:

* Kein DB-Zugriff — kein ``Plan``-Record nötig, ein synthetischer
  ``plan_id`` (UUID) wird pro Run generiert.
* Bypass der ``_store_extraction_result``-Persistierung — wir ziehen
  die Räume aus dem Vision-Output direkt in die Konsole.
* ``DEBUG_SAVE_CROPS=true`` wird vor dem Import von ``app.config``
  erzwungen, damit jede Render-Zwischenstufe auf Disk landet.

Damit kannst du visuell prüfen: hat die Haiku-BBox-Probe das Gebäude
richtig gefunden? Sieht der High-Res-Crop sauber aus? Kommen die
Räume jetzt mit hoher Konfidenz raus, oder findet die KI weiter
nichts?

Usage
=====

::

    cd backend
    # Wenn keine .env existiert, Key direkt in die Shell:
    #   PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."
    #   bash:       export ANTHROPIC_API_KEY=sk-ant-...

    .venv/Scripts/python.exe scripts/debug_two_pass.py /pfad/zum/plan.pdf
    .venv/Scripts/python.exe scripts/debug_two_pass.py /pfad/zum/plan.pdf --page 1

Exit codes
==========

* 0 — Run beendet, Output geprintet (egal ob Räume gefunden oder nicht).
* 2 — Falsche CLI-Argumente / PDF nicht gefunden / Key fehlt.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import sys
import uuid
from pathlib import Path

# Windows-Konsolen laufen oft auf cp1252; der Runner druckt Unicode
# (→, „", Umlaute). Reconfigure auf utf-8 verhindert UnicodeEncodeError
# auf den ersten print()-Calls. Kein Effekt unter Linux/macOS, wo
# stdout sowieso utf-8 ist.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# .env-Loader — VOR allem app-Import. ``settings`` instanziiert beim
# Modul-Load aus den ENV-Variablen; Setdefaults verhindern dass eine
# bereits gesetzte Shell-ENV von einer .env überschrieben wird.
# ---------------------------------------------------------------------------


def _load_dotenv_if_present() -> None:
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",          # backend/.env
        Path(__file__).resolve().parent.parent.parent / ".env",   # repo-root/.env
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
        break


_load_dotenv_if_present()

# Force snapshots ON for diagnostic runs. ``setdefault`` so the caller
# can explicitly set =false if they want a paranoid no-disk-write run.
os.environ.setdefault("DEBUG_SAVE_CROPS", "true")

# Make the backend ``app`` package importable when this script is run
# from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Now the imports that depend on env-vars being set.
from app.config import settings  # noqa: E402
from app.plan_analysis.pipeline import (  # noqa: E402
    _count_extracted_rooms,
    _extract_rooms_two_pass,
    _render_all_pages,
)


# ---------------------------------------------------------------------------
# Rendering / orchestrating
# ---------------------------------------------------------------------------


async def _run(pdf_path: Path, only_page: int | None) -> int:
    if not settings.anthropic_api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY ist nicht gesetzt.\n"
            "  - PowerShell:  $env:ANTHROPIC_API_KEY = \"sk-ant-...\"\n"
            "  - bash:        export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  - oder in backend/.env eintragen.",
            file=sys.stderr,
        )
        return 2

    if not settings.debug_save_crops:
        print(
            "WARN: settings.debug_save_crops=False — Snapshots werden NICHT "
            "geschrieben. (Sollte default true sein für diesen Runner.)",
            file=sys.stderr,
        )

    plan_id = uuid.uuid4()
    snapshot_dir = Path(settings.upload_dir) / "debug-crops" / str(plan_id)

    print("=" * 70)
    print("debug_two_pass — v24.4.6 Two-Pass-Plan-Analyse")
    print("=" * 70)
    print(f"PDF:           {pdf_path}")
    print(f"plan_id:       {plan_id}")
    print(f"snapshots →    {snapshot_dir}")
    print(f"DEBUG_SAVE_CROPS: {settings.debug_save_crops}")
    print()

    import fitz  # PyMuPDF — late import (file load may fail under restrictive policies)

    fitz_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix=f"fitz-{plan_id}"
    )
    loop = asyncio.get_event_loop()
    doc = None
    try:
        doc = await loop.run_in_executor(fitz_executor, fitz.open, str(pdf_path))
        rendered_pages, render_errors = await loop.run_in_executor(
            fitz_executor, _render_all_pages, doc,
        )
        print(
            f"Pre-Render: {len(rendered_pages)} Seite(n) gerendert, "
            f"{len(render_errors)} Render-Fehler."
        )
        if render_errors:
            for err in render_errors:
                print(f"  ! {err}")
        print()

        if not rendered_pages:
            print("Keine Seite konnte gerendert werden — Abbruch.", file=sys.stderr)
            return 1

        for page_number, fallback_bytes, fallback_mime in rendered_pages:
            if only_page is not None and page_number != only_page:
                continue

            print("-" * 70)
            print(f"Seite {page_number}")
            print("-" * 70)
            result = await _extract_rooms_two_pass(
                doc=doc,
                page_number=page_number,
                fitz_executor=fitz_executor,
                fallback_image_bytes=fallback_bytes,
                fallback_mime_type=fallback_mime,
                plan_id=plan_id,
            )

            # BBox-JSON aus dem Debug-Output lesen — verrät uns, ob
            # ein Fail-Safe gegriffen hat und auf welche Begründung.
            bbox_json_path = snapshot_dir / f"page-{page_number}-bbox.json"
            if bbox_json_path.exists():
                bbox_data = json.loads(bbox_json_path.read_text(encoding="utf-8"))
                reason = bbox_data.get("fail_safe_reason")
                bbox = bbox_data.get("bbox")
                low_res_dims = bbox_data.get("low_res_dims")
                if low_res_dims:
                    print(f"  Low-Res-Bild:    {low_res_dims[0]} × {low_res_dims[1]} px")
                if bbox:
                    print(
                        f"  Haiku-BBox:      x={bbox.get('x')} y={bbox.get('y')} "
                        f"w={bbox.get('width')} h={bbox.get('height')}"
                    )
                if reason:
                    print(f"  Fail-Safe:       {reason} → Fallback auf Single-Pass")
                else:
                    print(f"  Fail-Safe:       —  (Two-Pass läuft durch)")

            if result is None:
                print("  ⚠ Ergebnis: None (Two-Pass + Fallback haben beide gefehlt)")
                print()
                continue

            n_rooms = _count_extracted_rooms(result)
            notes = result.get("notes") if isinstance(result, dict) else None
            print(f"  Erkannte Räume:  {n_rooms}")
            if notes:
                print(f"  KI-notes:        {notes!r}")

            if n_rooms > 0:
                # Konfidenz-Verteilung + Liste der Räume
                confidences: list[float] = []
                print()
                print("  Räume:")
                for unit in result.get("units") or []:
                    unit_name = unit.get("unit_name") or "—"
                    for room in unit.get("rooms") or []:
                        rn = room.get("room_name") or "?"
                        ar = room.get("area_m2")
                        pr = room.get("perimeter_m")
                        cf = room.get("confidence")
                        ft = room.get("floor_type")
                        if isinstance(cf, (int, float)):
                            confidences.append(float(cf))
                        ar_s = f"{ar:6.2f} m²" if isinstance(ar, (int, float)) else "    —    "
                        pr_s = f"{pr:6.2f} m"  if isinstance(pr, (int, float)) else "    —   "
                        cf_s = f"{cf:.2f}"     if isinstance(cf, (int, float)) else "  — "
                        ft_s = ft or "—"
                        print(
                            f"    [{unit_name:>6}] {rn:<40} "
                            f"A={ar_s}  U={pr_s}  conf={cf_s}  belag={ft_s}"
                        )

                if confidences:
                    avg = sum(confidences) / len(confidences)
                    lo = min(confidences)
                    hi = max(confidences)
                    print()
                    print(
                        f"  Konfidenz:       min={lo:.2f}  avg={avg:.2f}  "
                        f"max={hi:.2f}  (n={len(confidences)})"
                    )

            # Snapshot-Pfade auflisten — der User soll wissen, was zur
            # visuellen Inspektion bereit liegt.
            print()
            print("  Snapshots:")
            for stem in ("low_res", "high_res_crop", "resized", "tile_0", "tile_1", "tile_2", "tile_3"):
                p = snapshot_dir / f"page-{page_number}-{stem}.jpg"
                if p.exists():
                    size_kb = p.stat().st_size // 1024
                    print(f"    {p.name:<35} {size_kb:>5} KB  → {p}")
            if bbox_json_path.exists():
                print(f"    {bbox_json_path.name}  → {bbox_json_path}")

            print()

        return 0

    finally:
        if doc is not None:
            try:
                await loop.run_in_executor(fitz_executor, doc.close)
            except Exception:
                pass
        fitz_executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone runner für die v24.4.6 Two-Pass-Plan-Analyse. "
            "Druckt BBox + Räume + Konfidenz pro Seite und legt alle "
            "Render-Zwischenstufen unter {upload_dir}/debug-crops/{plan_id}/ "
            "ab — zur visuellen Inspektion ob der Crop wirklich gesessen hat."
        )
    )
    parser.add_argument(
        "pdf",
        help="Pfad zur PDF-Datei. Anführungszeichen verwenden wenn der Pfad Leerzeichen enthält.",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=None,
        help="Falls gesetzt: nur diese Seite (1-basiert) analysieren. Default: alle.",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"ERROR: PDF nicht gefunden: {pdf_path}", file=sys.stderr)
        sys.exit(2)
    if pdf_path.suffix.lower() != ".pdf":
        print(
            f"WARN: Datei hat keine .pdf-Endung ({pdf_path.suffix!r}) — "
            f"versuche trotzdem zu öffnen.",
            file=sys.stderr,
        )

    rc = asyncio.run(_run(pdf_path, args.page))
    sys.exit(rc)


if __name__ == "__main__":
    main()
