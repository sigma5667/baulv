"""Security-Regression — Path-Traversal in den Static-File-Handlern
(Audit 2026-06).

Lockt ``app.main._safe_static_file``. Vorher jointen die Handler
``/icons/{path}`` und der SPA-Catch-all ``{full_path}`` User-Input
ungeprüft an ``STATIC_DIR`` und lieferten das Ergebnis als
``FileResponse`` aus. Über ``..``-Sequenzen ließ sich aus
``/app/static`` herausnavigieren (z.B. ins danebenliegende
``/app/uploads`` mit hochgeladenen Plan-PDFs / Logos).

``_safe_static_file`` resolved den Pfad und gibt ihn nur zurück, wenn
er nach ``resolve()`` weiterhin INNERHALB von ``STATIC_DIR`` liegt UND
eine existierende reguläre Datei ist — sonst ``None``.

Wir monkeypatchen ``app.main.STATIC_DIR`` auf ein tmp-Verzeichnis, weil
der echte Default ``/app/static`` nur im Container existiert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.main as main


@pytest.fixture
def static_sandbox(tmp_path, monkeypatch):
    """Baut eine realistische Verzeichnisstruktur:

        <tmp>/static/index.html          (gültig)
        <tmp>/static/icons/icon-192.png  (gültig, verschachtelt)
        <tmp>/uploads/secret.pdf         (AUSSERHALB static — Traversal-Ziel)

    und zeigt ``app.main.STATIC_DIR`` auf ``<tmp>/static``.
    """
    static_dir = tmp_path / "static"
    (static_dir / "icons").mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (static_dir / "icons" / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    (uploads_dir / "secret.pdf").write_bytes(b"%PDF-1.4 geheim")

    monkeypatch.setattr(main, "STATIC_DIR", static_dir)
    return tmp_path


# ---------------------------------------------------------------------------
# Positiv — legitime Dateien innerhalb von STATIC_DIR
# ---------------------------------------------------------------------------


def test_serves_top_level_file(static_sandbox):
    result = main._safe_static_file("index.html")
    assert result is not None
    assert result.name == "index.html"
    # Muss innerhalb von STATIC_DIR liegen.
    assert result.is_relative_to((static_sandbox / "static").resolve())


def test_serves_nested_file(static_sandbox):
    result = main._safe_static_file("icons", "icon-192.png")
    assert result is not None
    assert result.name == "icon-192.png"
    assert result.is_relative_to((static_sandbox / "static").resolve())


# ---------------------------------------------------------------------------
# Traversal — muss None liefern (Containment)
# ---------------------------------------------------------------------------


def test_rejects_parent_traversal_to_uploads(static_sandbox):
    """``../uploads/secret.pdf`` resolved nach außerhalb static →
    None, obwohl die Datei real existiert und lesbar ist."""
    # Die Datei existiert wirklich — wir prüfen, dass der Containment-
    # Check greift, nicht bloß ein "file not found".
    assert (static_sandbox / "uploads" / "secret.pdf").is_file()

    result = main._safe_static_file("..", "uploads", "secret.pdf")
    assert result is None


def test_rejects_parent_traversal_single_string(static_sandbox):
    """Der SPA-Catch-all übergibt EINEN String (``full_path``). Eine
    eingebettete ``../``-Sequenz darin muss ebenso geblockt werden."""
    result = main._safe_static_file("../uploads/secret.pdf")
    assert result is None


def test_rejects_traversal_from_nested_segment(static_sandbox):
    """Der icons-Handler ruft ``_safe_static_file("icons", path)``. Ein
    ``path`` mit ``../..``-Ausbruch muss geblockt werden."""
    result = main._safe_static_file("icons", "../../uploads/secret.pdf")
    assert result is None


# ---------------------------------------------------------------------------
# Sonstige None-Fälle
# ---------------------------------------------------------------------------


def test_rejects_nonexistent_file(static_sandbox):
    assert main._safe_static_file("does-not-exist.txt") is None


def test_rejects_directory(static_sandbox):
    """``icons`` ist ein Verzeichnis, keine Datei → None (sonst würde
    ein FileResponse auf ein Directory crashen)."""
    assert main._safe_static_file("icons") is None


def test_rejects_static_root_itself(static_sandbox):
    """Leeres parts → STATIC_DIR selbst (ein Verzeichnis) → None."""
    assert main._safe_static_file("") is None
