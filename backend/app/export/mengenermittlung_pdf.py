"""PDF-Export der Mengenermittlung für ein Projekt.

Sister of ``pdf_exporter.py`` (which is for LVs). Same reportlab
toolchain, A4 portrait, conservative margins. Built on top of the
calculation-engine's room data — no AI calls, no DB writes.

Layout (v24.3 — nach Profi-Feedback überarbeitet)
==================================================

  1. Header — Logo (oder "BauLV"-Text-Fallback) oben links, Titel
     und Untertitel rechts daneben.
  2. Disclaimer-Kasten — dezent gerahmter Hinweis "Diese
     Mengenermittlung ist eine Vorabkalkulation und ersetzt keine
     geprüfte Berechnung."
  3. Metadaten-Block — Projekt, Adresse, Auftraggeber etc. Leere
     Felder werden komplett weggelassen (statt em-dash).
  4. Übersichts-Tabelle — eine Zeile pro Raum (Name, Geschoss,
     Fläche, Umfang, Höhe, Wand brutto/netto, Volumen).
  5. Detail-Nachweis pro Raum — Formel-Block + kleine Skizze.
  6. Summen — Anzahl Räume, Σ Boden / Decke / Wand brutto / netto,
     Σ Volumen.
  7. Footer (jede Seite) — "Erstellt mit BauLV" links, Disclaimer
     mittig, "Seite X von N" rechts. KEINE internen Versions-
     Strings (Build-Tags, Branch-Namen) im Footer — Profi-Feedback
     v24.3 hatte solche Marker als unprofessionell markiert.

Privacy / DSGVO
===============

Das PDF enthält nur die Daten, die der User selbst eingegeben oder
über die KI-Plananalyse erfasst hat. Es werden keine personen-
bezogenen Daten Dritter geladen. Der Footer trägt einen expliziten
Hinweis: "Vorabkalkulations-Standards — keine rechtsverbindliche
Mengenermittlung".
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.plan import Plan
from app.db.models.project import (
    Building,
    Floor,
    Opening,
    Project,
    Room,
    Unit,
)
from app.db.models.user import User


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v24.3 — Branding/Logo constants
# ---------------------------------------------------------------------------
#
# Logo-Box im Header. 40 mm × 20 mm folgt der Spec ("max 4 cm × 2 cm,
# Aspect-Ratio erhalten"). reportlab's Image kann mit ``preserveAspectRatio``
# unter beiden Werten skalieren — kürzere Seite wird volle Box-Höhe/Breite,
# die andere proportional reduziert.
_LOGO_MAX_WIDTH_MM = 40.0
_LOGO_MAX_HEIGHT_MM = 20.0


# ---------------------------------------------------------------------------
# Helpers — German number formatting + safe text escape
# ---------------------------------------------------------------------------


def _safe(text: object) -> str:
    """Escape text for reportlab Paragraph (XML-like markup parser)."""
    if text is None:
        return ""
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt(value: object, digits: int = 2) -> str:
    """German-style number with comma as decimal separator. None → em-dash.

    Tables (Übersicht, Summen, Detail-Formeln) call this with
    ``None`` for missing measurements and still need a visible
    placeholder — there an em-dash signals "value missing" inside
    the columnar layout. The metadata block uses ``_present_only``
    instead so unfilled rows disappear entirely; the choice is
    explicit per caller.
    """
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    formatted = f"{n:,.{digits}f}"
    # Two-step swap so "1,234.56" → "1.234,56" (German notation)
    return (
        formatted.replace(",", "")
        .replace(".", ",")
        .replace("", ".")
    )


def _floor_label(floor: Floor | None) -> str:
    """Compact German floor label for table cells.

    Maps ``level_number`` to common Austrian abbreviations when it's
    set; falls back to ``floor.name`` otherwise. Used in the
    Übersichtstabelle and in the detail header.
    """
    if floor is None:
        return "—"
    n = floor.level_number
    if n is None:
        return floor.name or "—"
    if n == 0:
        return "EG"
    if n < 0:
        return f"{abs(n)}.UG" if n != -1 else "KG"
    return f"{n}.OG"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def export_mengenermittlung_pdf(
    project_id: UUID,
    db: AsyncSession,
    creator: User | None = None,
) -> bytes:
    """Render the project's Mengenermittlung as a PDF. Returns raw bytes.

    Raises ``ValueError`` if the project is missing — the route handler
    translates that into a 404.

    v24.3 — The route handler (``api/projects.py``) refuses empty
    projects with a 400 before calling here; this function still
    handles the no-rooms case gracefully by simply skipping the
    Übersicht/Detail/Summen sections (defence-in-depth, the API
    guard is the primary protection).

    ``author`` metadata is fixed to the literal ``"BauLV"`` — no
    internal version markers in PDF metadata or footer (v24.3
    Profi-Feedback explicitly called out "v23.9-claude"-style
    debug artefacts as unprofessional).
    """
    # Eager-load the building tree + plans so we render in one
    # session and don't trip async-greenlet errors.
    project_stmt = (
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.buildings)
            .selectinload(Building.floors)
            .selectinload(Floor.units)
            .selectinload(Unit.rooms)
            .selectinload(Room.openings),
            selectinload(Project.plans),
        )
    )
    project = (await db.execute(project_stmt)).scalars().first()
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    # Flatten the room tree, carrying the floor/unit context for each
    # room so the table cells can show "Wohnzimmer · EG · Top 1".
    rooms_with_context = list(_iter_rooms(project))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=22 * mm,
        title=f"Mengenermittlung — {project.name}",
        # v24.3 — author stays exactly "BauLV". No build tag, no
        # version, no branch name. PDF metadata is just as user-
        # visible as the footer once the file leaves the browser.
        author="BauLV",
        creator="BauLV",
    )

    styles = _build_styles()
    story: list = []

    _append_header(story, project, creator, styles)
    _append_disclaimer_box(story, styles)
    _append_metadata_block(story, project, creator, styles)
    _append_plan_references(story, project, styles)

    if rooms_with_context:
        _append_overview_table(story, rooms_with_context, styles)
        _append_room_details(story, rooms_with_context, styles)
        _append_summary(story, rooms_with_context, styles)

    # v24.3 — Two-pass page numbering via NumberedCanvas so the
    # footer can render "Seite X von N" instead of the bare
    # "Seite X" of the old build.
    doc.build(
        story,
        canvasmaker=_NumberedCanvas,
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Tree walk — flatten Project → Building → Floor → Unit → Room
# ---------------------------------------------------------------------------


def _iter_rooms(
    project: Project,
) -> Iterable[tuple[Room, Floor | None, Unit | None]]:
    """Yield every room with its floor/unit context, ordered for
    natural reading: building.sort_order → floor.level_number →
    unit.sort_order → room.name. Returned tuples carry ``floor`` and
    ``unit`` references because the room table cells need them for
    labels."""
    buildings = sorted(
        project.buildings or [],
        key=lambda b: (b.sort_order if b.sort_order is not None else 0, b.name or ""),
    )
    for building in buildings:
        floors = sorted(
            building.floors or [],
            key=lambda f: (
                # Lowest level (Keller) first, then EG, then upper floors.
                # NULL level_number sorts last so unconventional floors
                # don't break the order.
                (f.level_number is None, f.level_number or 0),
                f.name or "",
            ),
        )
        for floor in floors:
            units = sorted(
                floor.units or [],
                key=lambda u: (
                    u.sort_order if u.sort_order is not None else 0,
                    u.name or "",
                ),
            )
            for unit in units:
                rooms = sorted(
                    unit.rooms or [],
                    key=lambda r: r.name or "",
                )
                for room in rooms:
                    yield room, floor, unit


# ---------------------------------------------------------------------------
# Styles — keep close to ``pdf_exporter`` so docs read like a family
# ---------------------------------------------------------------------------


def _build_styles():
    base = getSampleStyleSheet()
    base.add(
        ParagraphStyle(
            name="METitle",
            parent=base["Heading1"],
            fontSize=16,
            spaceAfter=4,
            textColor=colors.HexColor("#1f2937"),
        )
    )
    base.add(
        ParagraphStyle(
            name="MESubtitle",
            parent=base["Normal"],
            fontSize=10,
            leading=12,
            spaceAfter=10,
            textColor=colors.HexColor("#6b7280"),
        )
    )
    base.add(
        ParagraphStyle(
            name="MEMeta",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
        )
    )
    base.add(
        ParagraphStyle(
            name="MESection",
            parent=base["Heading3"],
            fontSize=11,
            spaceBefore=10,
            spaceAfter=4,
            textColor=colors.HexColor("#1e40af"),
        )
    )
    base.add(
        ParagraphStyle(
            name="MERoomTitle",
            parent=base["Heading4"],
            fontSize=10,
            spaceBefore=6,
            spaceAfter=2,
            textColor=colors.HexColor("#1f2937"),
        )
    )
    base.add(
        ParagraphStyle(
            name="MEFormula",
            parent=base["Normal"],
            fontName="Courier",
            fontSize=8,
            leading=10,
            leftIndent=4,
            textColor=colors.HexColor("#374151"),
        )
    )
    base.add(
        ParagraphStyle(
            name="MEFormulaLabel",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            leftIndent=4,
            textColor=colors.HexColor("#6b7280"),
        )
    )
    base.add(
        ParagraphStyle(
            name="MESummaryLabel",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
        )
    )
    # v24.3 — Disclaimer-Box auf Seite 1. Etwas kleiner als der
    # Metadaten-Block damit der Kasten dezent wirkt, aber gross
    # genug zum tatsaechlichen Lesen (im Footer-7.5pt wurde der
    # Hinweis verlaesslich uebersehen).
    base.add(
        ParagraphStyle(
            name="MEDisclaimer",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#1f2937"),
        )
    )
    return base


# ---------------------------------------------------------------------------
# v24.3 — Header (Logo + Titel) + Disclaimer-Box + Metadata + Plan-Refs
# ---------------------------------------------------------------------------


def _format_creator_label(creator: User | None) -> str:
    """Build the "Erstellt von" string per v24.3 spec.

    Priority:

      1. ``full_name`` + ``company_name`` → "Max Mustermann, Bau GmbH"
      2. ``full_name`` alone               → "Max Mustermann"
      3. Email fallback                    → "max@example.com"

    The username (the part before the @ in the email) is **never**
    used as a display label — Profi-Feedback v24.3 explicitly
    flagged "Erstellt von: kafjd" as unprofessional.

    ``role`` (Funktion, e.g. "Bautraeger") is appended in
    parentheses when set so a small architect office can identify
    itself as "Erika Beispiel, Beispiel Architekten ZT GmbH
    (Planverfasserin)" without re-using the company field.
    """
    if creator is None:
        return ""
    parts: list[str] = []
    name = (creator.full_name or "").strip()
    company = (creator.company_name or "").strip() if creator.company_name else ""
    role = (creator.role or "").strip() if creator.role else ""
    email = (creator.email or "").strip()

    if name and company:
        parts.append(f"{name}, {company}")
    elif name:
        parts.append(name)
    elif email:
        parts.append(email)
    # No silent username fallback. If somehow nothing's set, the
    # "Erstellt von" row simply isn't rendered (see
    # ``_append_metadata_block``).

    if parts and role:
        parts[0] = f"{parts[0]} ({role})"

    return parts[0] if parts else ""


def _build_logo_flowable(creator: User | None) -> Image | Paragraph:
    """Top-left header flowable. Returns the user's logo image if
    one is uploaded; otherwise a small "BauLV" text-logo.

    The reportlab ``Image`` is created with explicit width/height
    so the cell-layout knows its bounds — ``preserveAspectRatio``
    keeps the upload's aspect ratio inside the 40×20 mm box.

    Any error reading the logo (file missing, decoder rejected the
    bytes) falls back to the text-logo silently. We log a warning
    but don't surface to the user — a PDF that still renders is
    better than a 500 because someone deleted an image file
    behind the server's back.
    """
    if creator is not None and creator.logo_path:
        try:
            path = Path(creator.logo_path)
            if path.is_file():
                img = Image(
                    str(path),
                    width=_LOGO_MAX_WIDTH_MM * mm,
                    height=_LOGO_MAX_HEIGHT_MM * mm,
                    kind="proportional",
                )
                # ``hAlign`` left-anchors the image inside the
                # outer container so a square logo doesn't drift
                # to centre when the box width is wider than the
                # scaled image.
                img.hAlign = "LEFT"
                return img
        except Exception as e:  # noqa: BLE001
            # Image() can raise on corrupt files, missing PIL, or
            # unreadable bytes — degrade to the text-logo rather
            # than fail the whole render.
            logger.warning(
                "logo embed failed user=%s path=%s err=%s",
                getattr(creator, "id", "?"),
                creator.logo_path,
                e,
            )

    # Fallback "BauLV" wordmark. Small, discreet — the spec calls
    # this "klein, unauffällig".
    return Paragraph(
        "<font size=14 color='#1f2937'><b>BauLV</b></font>",
        ParagraphStyle("MELogoText", fontSize=14, leading=16),
    )


def _append_header(
    story: list,
    project: Project,
    creator: User | None,
    styles,
) -> None:
    """Top of page 1: logo on the left, title block on the right.

    Two-column Table so the logo and title sit side-by-side at
    consistent vertical alignment regardless of the logo's actual
    height. The right column carries the title + subtitle stack.
    """
    logo_flowable = _build_logo_flowable(creator)
    title_block = [
        Paragraph("MENGENERMITTLUNG", styles["METitle"]),
        Paragraph(
            "Vorabkalkulation nach branchenüblichen Mengenermittlungs-Standards",
            styles["MESubtitle"],
        ),
    ]

    header_row = Table(
        [[logo_flowable, title_block]],
        colWidths=[45 * mm, 120 * mm],
    )
    header_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(header_row)


def _append_disclaimer_box(story: list, styles) -> None:
    """Single-cell bordered box right under the header.

    Pre-v24.3 the disclaimer lived only in the footer's 7.5pt grey
    font — easy to overlook. Profi-Feedback marked this as a risk
    ("Subunternehmer könnte das versehentlich erhalten und gegen
    den Bauträger verwenden"). The box raises visibility without
    being shouty: 1px dezent-graue Linie, 5mm Padding.
    """
    story.append(Spacer(1, 4 * mm))
    disclaimer = Paragraph(
        "<b>Hinweis:</b> Diese Mengenermittlung ist eine "
        "Vorabkalkulation und ersetzt keine geprüfte Berechnung. "
        "Die Werte basieren auf den im Projekt erfassten Räumen "
        "und sind als Anhaltspunkt für die Kalkulation gedacht.",
        styles["MEDisclaimer"],
    )
    box = Table(
        [[disclaimer]],
        colWidths=[165 * mm],
    )
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#9ca3af")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9fafb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]
        )
    )
    story.append(box)


def _append_metadata_block(
    story: list,
    project: Project,
    creator: User | None,
    styles,
) -> None:
    """Two-column Projekt-Metadaten table. Empty fields are
    **omitted**, not rendered with em-dash placeholders.

    v24.3 — pre-v24.3 unfilled rows showed "Adresse: —" /
    "Auftraggeber: —" which Profi-Feedback marked as visual noise
    in a cover document. Skipping the entire row keeps the block
    tight on projects that only have a name + creator filled in.
    """
    story.append(Spacer(1, 4 * mm))
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    creator_label = _format_creator_label(creator)
    # Tuples of (label, value). Empty/None values are filtered out
    # below so unfilled rows simply don't render.
    raw_rows: list[tuple[str, str | None]] = [
        ("Projekt", project.name),
        ("Adresse", project.address),
        ("Auftraggeber", project.client_name),
        ("Projektnummer", project.project_number),
        ("Grundstücksnr.", project.grundstuecksnr),
        ("Planverfasser", project.planverfasser),
        ("Erstellt am", today),
        ("Erstellt von", creator_label or None),
    ]
    rows = [(label, value) for label, value in raw_rows if value]
    if not rows:
        return

    meta_rows = [
        [
            Paragraph(f"<b>{_safe(label)}:</b>", styles["MEMeta"]),
            Paragraph(_safe(value), styles["MEMeta"]),
        ]
        for label, value in rows
    ]
    meta_table = Table(meta_rows, colWidths=[35 * mm, 130 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(meta_table)


def _append_plan_references(
    story: list,
    project: Project,
    styles,
) -> None:
    """Plan-Referenzen-Tabelle (falls Plaene hochgeladen wurden).

    Separat vom Metadaten-Block weil das eine richtige Tabelle ist,
    keine Key-Value-Liste.
    """
    plans = list(project.plans or [])
    if not plans:
        return
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Plan-Referenzen", styles["MESection"]))
    plan_rows = [["#", "Dateiname", "Seiten", "Status"]]
    for idx, plan in enumerate(plans, start=1):
        plan_rows.append(
            [
                str(idx),
                _safe(plan.filename or "—")[:80],
                str(plan.page_count) if plan.page_count is not None else "—",
                _safe(plan.analysis_status or "—"),
            ]
        )
    plan_table = Table(
        plan_rows,
        colWidths=[10 * mm, 100 * mm, 25 * mm, 30 * mm],
        repeatRows=1,
    )
    plan_table.setStyle(_compact_table_style())
    story.append(plan_table)


# ---------------------------------------------------------------------------
# Übersichts-Tabelle — eine Zeile pro Raum
# ---------------------------------------------------------------------------


def _append_overview_table(
    story: list,
    rooms_with_context: list[tuple[Room, Floor | None, Unit | None]],
    styles,
) -> None:
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Übersicht aller Räume", styles["MESection"]))

    header = [
        "Raum",
        "Geschoss",
        "Fläche\n(m²)",
        "Umfang\n(m)",
        "Höhe\n(m)",
        "Wand brutto\n(m²)",
        "Wand netto\n(m²)",
        "Volumen\n(m³)",
    ]
    rows: list[list[str]] = [header]
    for room, floor, unit in rooms_with_context:
        volume = _volume(room)
        rows.append(
            [
                _safe(room.name or "—"),
                _safe(_floor_label(floor)),
                _fmt(room.area_m2, 2),
                _fmt(room.perimeter_m, 2),
                _fmt(room.height_m, 2),
                _fmt(room.wall_area_gross_m2, 2),
                _fmt(room.wall_area_net_m2, 2),
                _fmt(volume, 2),
            ]
        )
    table = Table(
        rows,
        colWidths=[
            42 * mm,  # Raum
            16 * mm,  # Geschoss
            16 * mm,  # Fläche
            16 * mm,  # Umfang
            14 * mm,  # Höhe
            22 * mm,  # Wand brutto
            22 * mm,  # Wand netto
            18 * mm,  # Volumen
        ],
        repeatRows=1,
    )
    table.setStyle(_overview_table_style())
    story.append(table)


# ---------------------------------------------------------------------------
# Detail-Nachweis pro Raum — Formel-Block + Skizze
# ---------------------------------------------------------------------------


def _append_room_details(
    story: list,
    rooms_with_context: list[tuple[Room, Floor | None, Unit | None]],
    styles,
) -> None:
    story.append(PageBreak())
    story.append(Paragraph("Berechnungs-Nachweise", styles["MESection"]))
    story.append(
        Paragraph(
            "Pro Raum: Wand- und Bodenflächen-Berechnung mit den "
            "verwendeten Eingaben und Abzügen.",
            styles["MEMeta"],
        )
    )
    story.append(Spacer(1, 3 * mm))

    for room, floor, unit in rooms_with_context:
        # Each room rendered as a KeepTogether block so a room's
        # title + sketch + formulas don't get split across page
        # breaks unless the block itself is taller than a page.
        flowables = _build_room_detail(room, floor, unit, styles)
        story.append(KeepTogether(flowables))
        story.append(Spacer(1, 4 * mm))


def _build_room_detail(
    room: Room,
    floor: Floor | None,
    unit: Unit | None,
    styles,
) -> list:
    """Return the flowables for one room's detail block."""
    title_parts = [room.name or "Unbenannt"]
    if floor is not None:
        title_parts.append(_floor_label(floor))
    if unit is not None and unit.name:
        title_parts.append(unit.name)
    title = " · ".join(_safe(p) for p in title_parts if p)
    if room.room_type:
        title += f" <font color='#6b7280'>(Typ: {_safe(room.room_type)})</font>"

    out: list = [Paragraph(title, styles["MERoomTitle"])]

    # Sketch (left) + formula list (right) as a 2-column row.
    sketch_cell = _build_sketch(room)
    formula_cell = _build_formulas(room, styles)
    side_by_side = Table(
        [[sketch_cell, formula_cell]],
        colWidths=[35 * mm, 130 * mm],
    )
    side_by_side.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    out.append(side_by_side)
    return out


def _build_sketch(room: Room) -> Table:
    """Render a small "sketch" placeholder for the room.

    Two modes:

    * ``bbox_width`` and ``bbox_height`` available → draw a
      proportional rectangle (max 25 mm in either dimension), so the
      reader sees roughly what shape the room has on the plan.
    * Otherwise → a default 22×22 mm square with the ``room_type``
      (or "Raum") as label, so every room block has visual weight
      even when there's no plan-derived shape.

    Returns a reportlab Table because raw shapes don't reflow inside
    KeepTogether — wrapping in a sized Table cell is the path of
    least resistance.
    """
    has_bbox = (
        room.bbox_width is not None
        and room.bbox_height is not None
        and room.bbox_width > 0
        and room.bbox_height > 0
    )
    label = (
        (room.room_type or "Raum")[:14]
        if not has_bbox
        else (room.name or "")[:14]
    )

    # Compute box dimensions (mm). Maintain a max-25mm bounding so
    # the cell doesn't outrun the column width.
    if has_bbox:
        bw = float(room.bbox_width)
        bh = float(room.bbox_height)
        scale = 25.0 / max(bw, bh)
        w_mm = max(8.0, bw * scale)
        h_mm = max(8.0, bh * scale)
    else:
        w_mm = 22.0
        h_mm = 22.0

    cell = Table(
        [[label]],
        colWidths=[w_mm * mm],
        rowHeights=[h_mm * mm],
    )
    cell.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#9ca3af")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f4f6")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 6.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#374151")),
            ]
        )
    )
    return cell


def _build_formulas(room: Room, styles) -> list:
    """Return the formula paragraphs for one room.

    Five potential lines (each only emitted when the inputs are
    populated, so an empty room renders an empty stack):

      1. Bodenfläche / Deckenfläche = area_m2 (direct)
      2. Wand brutto = perimeter × height
      3. Abzüge: Σ openings ≥ minimum-area
      4. Wand netto = brutto − Σ Abzüge × applied_factor
      5. Raum-Volumen = area × height
    """
    out: list = []

    if room.area_m2 is not None:
        out.append(
            Paragraph(
                f"Boden-/Deckenfläche = <b>{_fmt(room.area_m2, 2)} m²</b>",
                styles["MEFormula"],
            )
        )

    p = room.perimeter_m
    h = room.height_m
    if p is not None and h is not None:
        gross = float(p) * float(h)
        out.append(
            Paragraph(
                f"Wandfläche brutto = Umfang × Höhe = "
                f"{_fmt(p, 2)} m × {_fmt(h, 2)} m = "
                f"<b>{_fmt(gross, 2)} m²</b>",
                styles["MEFormula"],
            )
        )
    elif p is not None and h is None:
        # v24.3.1 — sichtbarer Hinweis statt stillem Skip.
        # Pre-v24.3.1 entfiel die Brutto-Formel kommentarlos wenn
        # ``height_m`` NULL war — der Leser sah eine Boden-/Decken-
        # flaeche und sonst nichts und musste raten, ob das
        # gewollt war. Jetzt rendert eine kursive Notiz, dass die
        # Berechnung uebersprungen wurde und welcher Eingabewert
        # fehlt.
        out.append(
            Paragraph(
                "<i>Wandfläche brutto = Umfang × Höhe — "
                "<b>Raumhöhe fehlt</b>; bitte ergänzen, damit der "
                "Nachweis berechnet werden kann.</i>",
                styles["MEFormulaLabel"],
            )
        )
    elif p is None and h is not None:
        out.append(
            Paragraph(
                "<i>Wandfläche brutto = Umfang × Höhe — "
                "<b>Umfang fehlt</b>; bitte ergänzen, damit der "
                "Nachweis berechnet werden kann.</i>",
                styles["MEFormulaLabel"],
            )
        )

    deductions = _deductions(room)
    if deductions["entries"]:
        line = "Abzüge: " + "; ".join(
            f"{e['label']} ({_fmt(e['width'], 2)} × {_fmt(e['height'], 2)} m"
            f"{' × ' + str(e['count']) if e['count'] != 1 else ''}) "
            f"= {_fmt(e['area'], 2)} m²"
            for e in deductions["entries"]
        )
        line += f" → Σ <b>{_fmt(deductions['total'], 2)} m²</b>"
        out.append(Paragraph(line, styles["MEFormula"]))
    elif room.openings:
        out.append(
            Paragraph(
                "Abzüge: keine (alle Öffnungen unter Mindestabzugs-Fläche)",
                styles["MEFormulaLabel"],
            )
        )

    if room.wall_area_net_m2 is not None:
        factor_note = ""
        if room.applied_factor is not None and float(room.applied_factor) != 1.0:
            factor_note = (
                f" (mit Faktor {_fmt(room.applied_factor, 2)} für "
                f"{'Treppenhaus' if room.is_staircase else 'Höhenzuschlag'})"
            )
        out.append(
            Paragraph(
                f"Wandfläche netto = "
                f"<b>{_fmt(room.wall_area_net_m2, 2)} m²</b>{factor_note}",
                styles["MEFormula"],
            )
        )

    if room.area_m2 is not None and room.height_m is not None:
        vol = float(room.area_m2) * float(room.height_m)
        out.append(
            Paragraph(
                f"Volumen = Fläche × Höhe = {_fmt(room.area_m2, 2)} m² × "
                f"{_fmt(room.height_m, 2)} m = <b>{_fmt(vol, 3)} m³</b>",
                styles["MEFormula"],
            )
        )

    if not out:
        out.append(
            Paragraph(
                "Keine Maße erfasst — bitte Fläche, Umfang und Höhe "
                "ergänzen, damit der Nachweis berechnet werden kann.",
                styles["MEFormulaLabel"],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Summen-Block am Ende
# ---------------------------------------------------------------------------


def _append_summary(
    story: list,
    rooms_with_context: list[tuple[Room, Floor | None, Unit | None]],
    styles,
) -> None:
    rooms = [r for r, _, _ in rooms_with_context]
    sum_floor = sum(
        float(r.area_m2) for r in rooms if r.area_m2 is not None
    )
    sum_ceiling = sum_floor  # Decke = Boden in der Berechnungs-Konvention
    sum_wall_gross = sum(
        float(r.wall_area_gross_m2)
        for r in rooms
        if r.wall_area_gross_m2 is not None
    )
    sum_wall_net = sum(
        float(r.wall_area_net_m2)
        for r in rooms
        if r.wall_area_net_m2 is not None
    )
    sum_volume = sum(
        float(r.area_m2) * float(r.height_m)
        for r in rooms
        if r.area_m2 is not None and r.height_m is not None
    )

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Summen", styles["MESection"]))
    summary_rows = [
        ["Anzahl Räume", str(len(rooms))],
        ["Σ Bodenfläche", f"{_fmt(sum_floor, 2)} m²"],
        ["Σ Deckenfläche", f"{_fmt(sum_ceiling, 2)} m²"],
        ["Σ Wandfläche brutto", f"{_fmt(sum_wall_gross, 2)} m²"],
        ["Σ Wandfläche netto", f"{_fmt(sum_wall_net, 2)} m²"],
        ["Σ Raum-Volumen", f"{_fmt(sum_volume, 3)} m³"],
    ]
    summary_table = Table(
        summary_rows,
        colWidths=[55 * mm, 50 * mm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (1, 0), (1, -1), "Courier-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e5e7eb")),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.HexColor("#1f2937")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.append(summary_table)


# ---------------------------------------------------------------------------
# v24.3 — Footer via NumberedCanvas (two-pass page counting)
# ---------------------------------------------------------------------------
#
# Pre-v24.3 the footer rendered "Seite 1" — reportlab's running
# page counter is only the CURRENT page; the total isn't known
# until the document is fully laid out. The canonical reportlab
# recipe for "Seite X von N" is a Canvas subclass that buffers
# every showPage() into a state list, then on save() walks the
# list and draws the footer with len(states) as the total.
#
# Profi-Feedback (v24.3) explicitly asked for "Seite X von N";
# Feedback-Punkt 6 in the spec.


class _NumberedCanvas(Canvas):
    """Canvas subclass that defers per-page rendering until the
    total page count is known.

    Implementation pattern straight from the reportlab user-guide
    recipe for total-page numbering — we override ``showPage`` to
    snapshot the state of each page, and ``save`` to walk the
    snapshots and draw the footer with the now-known total.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 — reportlab API spelling
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            _draw_footer(self, total)
            super().showPage()
        super().save()


def _draw_footer(canvas: Canvas, total_pages: int) -> None:
    """Three-column footer: software identifier (left), Vorab-
    kalkulations-Disclaimer (centre), "Seite X von N" (right).

    v24.3 — explicitly NO internal version strings here. The
    pre-v24.3 footer was already clean ("Erstellt mit BauLV") but
    Profi-Feedback flagged "v23.9-claude"-style markers as a
    long-running anti-pattern; this comment locks the contract so
    a future refactor doesn't reintroduce them.
    """
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#6b7280"))

    width = A4[0]
    bottom_y = 12 * mm

    canvas.drawString(18 * mm, bottom_y, "Erstellt mit BauLV")
    canvas.drawCentredString(
        width / 2,
        bottom_y,
        "Vorabkalkulation — keine rechtsverbindliche Mengenermittlung",
    )
    page_num = canvas.getPageNumber()
    canvas.drawRightString(
        width - 18 * mm, bottom_y, f"Seite {page_num} von {total_pages}",
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Local helpers — table styles + deduction calc
# ---------------------------------------------------------------------------


def _overview_table_style() -> TableStyle:
    return TableStyle(
        [
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("FONTNAME", (2, 1), (-1, -1), "Courier"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#9ca3af")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def _compact_table_style() -> TableStyle:
    return TableStyle(
        [
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#9ca3af")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]
    )


# Deduction-threshold mirrors what the calculation engine uses for
# the wall trades (5.0 m² for plaster/concrete substrates). Kept
# private to this module because the PDF doesn't claim to re-run
# the calc engine — it's documenting what the user can see today
# in the "wall_area_net_m2" cached value.
_DEDUCTION_THRESHOLD_M2 = Decimal("5.0")


def _deductions(room: Room) -> dict:
    """Compute the deduction breakdown for the formula block.

    Mirrors the calculator's threshold-rule (openings ≥ 5.0 m² are
    deducted on plaster substrates) so the PDF shows the same
    numbers the user already sees in the Wandberechnungs-Tabelle.

    Returns ``{ "entries": [...], "total": float }`` where each
    entry is ``{ label, width, height, count, area }``.
    """
    entries: list[dict] = []
    total = Decimal("0")
    for o in room.openings or []:
        try:
            area = Decimal(str(o.width_m)) * Decimal(str(o.height_m)) * o.count
        except (TypeError, ValueError):
            continue
        if area >= _DEDUCTION_THRESHOLD_M2:
            entries.append(
                {
                    "label": o.opening_type or "Öffnung",
                    "width": float(o.width_m),
                    "height": float(o.height_m),
                    "count": int(o.count),
                    "area": float(area),
                }
            )
            total += area
    return {"entries": entries, "total": float(total)}


def _volume(room: Room) -> float | None:
    """Convenience wrapper. None when either input is missing."""
    if room.area_m2 is None or room.height_m is None:
        return None
    return float(room.area_m2) * float(room.height_m)
