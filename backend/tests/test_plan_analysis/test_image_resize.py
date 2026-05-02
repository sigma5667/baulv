"""Tests for the v23.1.2 image-resize loop and Anthropic-error translation.

Two surfaces:

1. ``_render_page_for_vision`` — the DPI/JPEG ladder that keeps
   rendered PDF pages under Anthropic's 5 MB image-payload limit.
   We fake the PyMuPDF pixmap so the test doesn't need a real PDF
   on disk; the size sequence is what's interesting, not the
   actual rendering.

2. ``_translate_anthropic_error`` — the friendly-message mapping
   for the three known failure modes (image-too-large,
   token-overflow, rate-limit). Pinning the exact German strings
   so a refactor doesn't silently dump the user back into the raw
   Anthropic JSON.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.plan_analysis.pipeline import (
    PlanAnalysisError,
    _format_page_error,
    _render_page_for_vision,
    _translate_anthropic_error,
    _VISION_DPI_LADDER,
    _VISION_IMAGE_MAX_BYTES,
)


# ---------------------------------------------------------------------------
# PyMuPDF mock — exposes a deterministic per-DPI/per-format size
# sequence so the test can shape exactly which path the resize loop
# takes without ever rendering a real PDF.
# ---------------------------------------------------------------------------


class _FakePixmap:
    """Returns pre-determined byte blobs for ``tobytes(output)``."""

    def __init__(self, png_size: int, jpeg_size: int):
        self._png = b"P" * png_size
        self._jpeg = b"J" * jpeg_size

    def tobytes(self, output: str = "png", jpg_quality: int = 95):
        if output == "png":
            return self._png
        if output in ("jpeg", "jpg"):
            return self._jpeg
        raise ValueError(f"Unexpected output={output!r}")


class _FakePage:
    """``get_pixmap(matrix)`` returns the next pixmap in a sequence.

    The sequence is what the test sets up: one entry per
    ``get_pixmap`` call. Each entry is a ``(png_size, jpeg_size)``
    tuple — the loop tries PNG first, then JPEG at the same DPI
    (one ``get_pixmap`` call per DPI), so a single tuple covers
    one DPI step.
    """

    def __init__(self, sizes: list[tuple[int, int]]):
        self._sizes = sizes
        self.calls = 0

    def get_pixmap(self, matrix):
        if self.calls >= len(self._sizes):
            raise AssertionError(
                f"_FakePage exhausted after {self.calls} renders — "
                f"the resize loop is dropping past the bottom of the DPI "
                f"ladder, which means the ladder length and the test "
                f"setup disagree."
            )
        png_size, jpeg_size = self._sizes[self.calls]
        self.calls += 1
        return _FakePixmap(png_size, jpeg_size)


@pytest.fixture(autouse=True)
def _stub_fitz_module(monkeypatch):
    """Avoid importing real PyMuPDF in the test process.

    ``_render_page_for_vision`` does ``import fitz`` for the
    ``Matrix`` constructor. We provide a stub module with a
    no-op ``Matrix`` so the test doesn't need PyMuPDF installed
    and we don't pay its import cost on every run.
    """
    fake_fitz = SimpleNamespace(Matrix=lambda a, b: ("matrix", a, b))
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)


# ---------------------------------------------------------------------------
# Resize-loop tests
# ---------------------------------------------------------------------------


def test_render_returns_png_at_first_dpi_when_small_enough():
    """Happy path: a small page renders to PNG at the highest DPI on
    the ladder (200 DPI) and ships immediately. No JPEG, no resize."""
    page = _FakePage([(_VISION_IMAGE_MAX_BYTES // 2, _VISION_IMAGE_MAX_BYTES // 4)])

    data, mime = _render_page_for_vision(page, page_number=1)

    assert mime == "image/png"
    assert len(data) == _VISION_IMAGE_MAX_BYTES // 2
    # Only one render attempt — no fallback needed.
    assert page.calls == 1


def test_render_falls_back_to_jpeg_when_png_too_big():
    """PNG at the first DPI is too big → JPEG at the same DPI fits.
    Mime flips to ``image/jpeg``; the loop did not drop the DPI."""
    too_big_png = _VISION_IMAGE_MAX_BYTES + 1_000_000
    fitting_jpeg = _VISION_IMAGE_MAX_BYTES // 2
    page = _FakePage([(too_big_png, fitting_jpeg)])

    data, mime = _render_page_for_vision(page, page_number=1)

    assert mime == "image/jpeg"
    assert len(data) == fitting_jpeg
    assert page.calls == 1


def test_render_drops_dpi_when_jpeg_still_too_big():
    """At 200 DPI both PNG and JPEG exceed the cap → loop drops to
    150 DPI and tries again. The second pixmap returns smaller
    PNG that fits, so we ship PNG/150."""
    too_big = _VISION_IMAGE_MAX_BYTES + 1_000_000
    fits = _VISION_IMAGE_MAX_BYTES // 2
    page = _FakePage([
        (too_big, too_big),  # 200 DPI: both formats too big
        (fits, fits // 2),    # 150 DPI: PNG fits
    ])

    data, mime = _render_page_for_vision(page, page_number=1)

    assert mime == "image/png"
    assert len(data) == fits
    # Two pixmap renders — one per DPI step.
    assert page.calls == 2


def test_render_walks_full_ladder_until_jpeg_fits():
    """Three DPI steps fail (PNG and JPEG both too big), the fourth
    is the floor (100 DPI) and JPEG there finally fits."""
    too_big = _VISION_IMAGE_MAX_BYTES + 1
    fits = _VISION_IMAGE_MAX_BYTES // 3

    sizes = [(too_big, too_big)] * (len(_VISION_DPI_LADDER) - 1)
    sizes.append((too_big, fits))  # at 100 DPI: PNG too big, JPEG fits

    page = _FakePage(sizes)
    data, mime = _render_page_for_vision(page, page_number=1)

    assert mime == "image/jpeg"
    assert len(data) == fits
    assert page.calls == len(_VISION_DPI_LADDER)


def test_render_raises_when_even_min_dpi_jpeg_too_big():
    """Plan is so dense that even 100 DPI JPEG can't compress under
    the cap → ``PlanAnalysisError`` with German user-message
    pointing at "split the PDF" or "lower export resolution"."""
    too_big = _VISION_IMAGE_MAX_BYTES + 1
    page = _FakePage([(too_big, too_big)] * len(_VISION_DPI_LADDER))

    with pytest.raises(PlanAnalysisError) as excinfo:
        _render_page_for_vision(page, page_number=1)

    msg = excinfo.value.detail
    assert "zu groß" in msg.lower()
    assert "auflösung" in msg.lower() or "bereiche" in msg.lower()


def test_render_respects_custom_max_bytes():
    """Caller-supplied ``max_bytes`` overrides the module default —
    needed so a future test (or a deliberately-tighter render in
    ops) can pin a smaller threshold without touching the module
    constant."""
    page = _FakePage([(2_000_000, 500_000)])

    # Tight threshold: 1 MB. PNG at 2 MB exceeds, JPEG at 500 KB fits.
    data, mime = _render_page_for_vision(
        page, page_number=1, max_bytes=1_000_000
    )

    assert mime == "image/jpeg"
    assert len(data) == 500_000


# ---------------------------------------------------------------------------
# Anthropic-error translation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_message",
    [
        "image exceeds 5 MB maximum: 6229616 bytes > 5242880 bytes",
        "Image exceeds maximum size of 5MB",
        "image too large for upload",
        "image dimension exceeds 7990 px (file too large)",
    ],
)
def test_translate_image_too_large_returns_german_user_message(raw_message):
    """All four variants of "image too large" map to the same
    actionable German message — exporting at lower resolution or
    splitting the PDF."""
    translated = _translate_anthropic_error(Exception(raw_message))
    assert translated is not None
    assert "zu groß" in translated.lower()
    assert (
        "auflösung" in translated.lower() or "bereiche" in translated.lower()
    )


@pytest.mark.parametrize(
    "raw_message",
    [
        "max_tokens insufficient for expected output",
        "context length exceeded for model",
        "context window of 200000 tokens exceeded",
    ],
)
def test_translate_token_overflow_returns_split_message(raw_message):
    translated = _translate_anthropic_error(Exception(raw_message))
    assert translated is not None
    assert "räume" in translated.lower() or "aufteilen" in translated.lower()


def test_translate_rate_limit_returns_wait_message():
    translated = _translate_anthropic_error(
        Exception("rate limit exceeded — please retry later")
    )
    assert translated is not None
    assert "warten" in translated.lower() or "moment" in translated.lower()


def test_translate_unknown_error_returns_none():
    """Anything we don't have a friendly translation for must return
    None so the caller falls back to the diagnostic ``ClassName —
    message`` format. Otherwise we'd be hiding novel failures behind
    a useless generic copy."""
    assert _translate_anthropic_error(Exception("kaboom")) is None
    assert _translate_anthropic_error(ValueError("unrelated parse error")) is None


# ---------------------------------------------------------------------------
# Format-page-error integrates the translation
# ---------------------------------------------------------------------------


def test_format_page_error_uses_friendly_translation_when_available():
    """Known image-too-large error → user-facing banner shows the
    German actionable copy, NO ``ClassName`` prefix."""
    out = _format_page_error(
        1, Exception("image exceeds 5 MB maximum: 6229616 bytes > 5242880 bytes")
    )
    assert "Seite 1: " in out
    assert "zu groß" in out.lower()
    # The diagnostic class-name format is suppressed for known errors.
    assert "Exception —" not in out


def test_format_page_error_falls_back_to_diagnostic_for_unknown():
    """Unknown errors keep the v23.1.1 diagnostic format so the
    operator can still parse what's going on from a screenshot."""
    out = _format_page_error(2, ValueError("something we never saw"))
    assert "Seite 2: ValueError" in out
    assert "something we never saw" in out
