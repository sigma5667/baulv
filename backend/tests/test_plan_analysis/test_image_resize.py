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
    """Returns pre-determined byte blobs for ``tobytes(output)``.

    Optionally raises an exception of a configurable type when
    asked for a particular format — used by the v23.1.3 tests to
    simulate ``RuntimeError`` from ``tobytes("jpeg")`` (the most
    common PyMuPDF failure mode in the wild: RGBA-source pages,
    exotic colorspaces, memory pressure).
    """

    def __init__(
        self,
        png_size: int,
        jpeg_size: int,
        *,
        png_raises: Exception | None = None,
        jpeg_raises: Exception | None = None,
    ):
        self._png = b"P" * png_size
        self._jpeg = b"J" * jpeg_size
        self._png_raises = png_raises
        self._jpeg_raises = jpeg_raises

    def tobytes(self, output: str = "png", jpg_quality: int = 95):
        if output == "png":
            if self._png_raises is not None:
                raise self._png_raises
            return self._png
        if output in ("jpeg", "jpg"):
            if self._jpeg_raises is not None:
                raise self._jpeg_raises
            return self._jpeg
        raise ValueError(f"Unexpected output={output!r}")


class _FakePage:
    """``get_pixmap(...)`` returns the next pixmap in a sequence.

    The sequence is what the test sets up: one entry per
    ``get_pixmap`` call. Each entry is either:
      * a ``_FakePixmap`` instance (full control), or
      * a ``(png_size, jpeg_size)`` tuple (compact form when no
        per-call exceptions are needed).
    """

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self.calls = 0
        # Capture the kwargs of every call so the tests can verify
        # we ask PyMuPDF for ``alpha=False`` + ``colorspace=csRGB``.
        self.last_kwargs: dict | None = None

    def get_pixmap(self, *, matrix=None, alpha=None, colorspace=None):
        if self.calls >= len(self._sequence):
            raise AssertionError(
                f"_FakePage exhausted after {self.calls} renders — "
                "the resize loop is dropping past the bottom of the DPI "
                "ladder, which means the ladder length and the test "
                "setup disagree."
            )
        entry = self._sequence[self.calls]
        self.calls += 1
        self.last_kwargs = {
            "matrix": matrix, "alpha": alpha, "colorspace": colorspace
        }
        if isinstance(entry, _FakePixmap):
            return entry
        png_size, jpeg_size = entry
        return _FakePixmap(png_size, jpeg_size)


@pytest.fixture(autouse=True)
def _stub_fitz_module(monkeypatch):
    """Avoid importing real PyMuPDF in the test process.

    ``_render_page_for_vision`` does ``import fitz`` for the
    ``Matrix`` constructor and the ``csRGB`` colorspace constant.
    We provide a stub module with both so the test doesn't need
    PyMuPDF installed and we don't pay its import cost on every
    run. ``csRGB`` is just a sentinel string; the test asserts the
    constant gets passed to ``get_pixmap`` but doesn't care about
    its actual identity.
    """
    fake_fitz = SimpleNamespace(
        Matrix=lambda a, b: ("matrix", a, b),
        csRGB="csRGB-sentinel",
    )
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


def test_render_passes_alpha_false_and_rgb_colorspace_to_pixmap():
    """v23.1.3: PyMuPDF must be asked for an RGB pixmap without
    alpha. Otherwise transparent PDFs (watermarks, modern CAD
    overlays) leak RGBA into the JPEG encoder, which raises a
    bare RuntimeError that the broader analyze_plan handler used
    to misclassify as 'PDF nicht öffenbar'. Belt-and-suspenders."""
    page = _FakePage([(100_000, 50_000)])

    _render_page_for_vision(page, page_number=1)

    assert page.last_kwargs is not None
    assert page.last_kwargs["alpha"] is False
    assert page.last_kwargs["colorspace"] == "csRGB-sentinel"


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


# ---------------------------------------------------------------------------
# v23.1.3 — render-error robustness
# ---------------------------------------------------------------------------


def test_render_skips_dpi_where_jpeg_encode_raises():
    """``pix.tobytes('jpeg')`` raising RuntimeError is the most
    common PyMuPDF in-the-wild failure mode (RGBA-source despite
    alpha=False, exotic colorspace). The loop must log + drop to
    the next DPI rather than aborting the page."""
    too_big = _VISION_IMAGE_MAX_BYTES + 1
    fits = _VISION_IMAGE_MAX_BYTES // 3

    page = _FakePage([
        # 200 DPI: PNG too big, JPEG raises RuntimeError → drop.
        _FakePixmap(too_big, 0, jpeg_raises=RuntimeError("RGBA not supported")),
        # 150 DPI: PNG fits.
        _FakePixmap(fits, fits // 2),
    ])

    data, mime = _render_page_for_vision(page, page_number=4)

    assert mime == "image/png"
    assert len(data) == fits
    assert page.calls == 2


def test_render_raises_specific_error_when_every_render_fails():
    """No DPI step produced any image (all render attempts raise).
    The fallback ``PlanAnalysisError`` must surface the underlying
    exception type and a snippet of its message so the operator
    can debug from the user's screenshot."""
    page = _FakePage([
        _FakePixmap(
            0, 0,
            png_raises=RuntimeError("colorspace conversion failed"),
            jpeg_raises=RuntimeError("colorspace conversion failed"),
        )
    ] * len(_VISION_DPI_LADDER))

    with pytest.raises(PlanAnalysisError) as excinfo:
        _render_page_for_vision(page, page_number=7)

    msg = excinfo.value.detail
    assert "Seite 7" in msg
    assert "konvertiert" in msg.lower()
    # Error class name surfaces so the operator sees the actual
    # underlying type, not just a generic message.
    assert "RuntimeError" in msg


def test_render_raises_with_pixmap_acquisition_error():
    """``page.get_pixmap()`` itself raising (corrupt page object,
    OOM) must also surface as a per-page render error rather than
    the misleading 'PDF nicht öffenbar' from v23.1.2's broader
    handler."""
    sentinel_msg = "page content stream malformed at offset 12345"

    class _ExplodingPage:
        last_kwargs = None
        calls = 0

        def get_pixmap(self, **kwargs):
            self.calls += 1
            self.last_kwargs = kwargs
            raise RuntimeError(sentinel_msg)

    page = _ExplodingPage()

    with pytest.raises(PlanAnalysisError) as excinfo:
        _render_page_for_vision(page, page_number=2)

    assert page.calls == len(_VISION_DPI_LADDER)
    assert "Seite 2" in excinfo.value.detail
    # Message + class name preserved through the chain.
    assert "RuntimeError" in excinfo.value.detail
    assert sentinel_msg[:30] in excinfo.value.detail


# ---------------------------------------------------------------------------
# v23.1.3 — _pdf_to_images: open vs render error separation
# ---------------------------------------------------------------------------


def test_pdf_to_images_collects_per_page_errors_without_aborting(monkeypatch):
    """A single unrenderable page must not abort the whole upload.
    The function returns ``(rendered, errors)`` where ``rendered``
    skips the failed page and ``errors`` collects its message —
    so the user gets two of three pages analysed plus a clear
    "Seite 2: …" entry instead of a blank-screen failure."""
    from app.plan_analysis import pipeline

    # Stub doc: three pages. Page 1 and 3 render fine; page 2
    # raises through the resize loop.
    fail_pixmap = _FakePixmap(
        0, 0,
        png_raises=RuntimeError("encode failed"),
        jpeg_raises=RuntimeError("encode failed"),
    )

    class _StubDoc:
        pages = [
            _FakePage([(100_000, 50_000)]),
            _FakePage([fail_pixmap] * len(_VISION_DPI_LADDER)),
            _FakePage([(120_000, 60_000)]),
        ]

        def __iter__(self):
            return iter(self.pages)

        def close(self):
            pass

    # Stitch ``open`` onto our fake fitz module that the autouse
    # fixture already installed in sys.modules.
    sys.modules["fitz"].open = lambda path: _StubDoc()

    rendered, errors = pipeline._pdf_to_images("dummy.pdf")

    assert [p[0] for p in rendered] == [1, 3]
    assert len(errors) == 1
    assert "Seite 2" in errors[0]


def test_pdf_to_images_propagates_open_failure(monkeypatch):
    """``fitz.open`` raising RuntimeError must propagate — the
    caller's ``analyze_plan`` handler maps it to "PDF nicht
    öffenbar". This is the open-vs-render boundary the v23.1.3
    fix is built around: open-errors crash through, render-errors
    get collected as per-page entries."""
    from app.plan_analysis import pipeline

    def _fail_open(path):
        raise RuntimeError("MuPDF: cannot recognize PDF format")

    sys.modules["fitz"].open = _fail_open

    with pytest.raises(RuntimeError, match="cannot recognize"):
        pipeline._pdf_to_images("corrupt.pdf")


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
