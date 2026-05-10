"""PDF-Export der Mengenermittlung für ein Projekt (v23.9).

Sister of ``pdf_exporter.py`` (which is for LVs). Same reportlab
toolchain, A4 portrait, conservative margins. Built on top of the
calculation-engine's room data — no AI calls, no DB writes.

Layout per spec
===============

  1. Header — Projektmetadaten, Plan-Referenzen, Datum + Ersteller
  2. Übersichts-Tabelle — eine Zeile pro Raum (Name, Geschoss,
     Fläche, Umfang, Höhe, Wand brutto/netto, Volumen)
  3. Detail-Nachweis pro Raum — Formel-Block mit der Berechnungs-
     Logik (Umfang × Höhe = brutto; minus Abzüge = netto), plus
     einer kleinen "Skizze" (proportionierter Box, falls bbox-
     Koordinaten vorhanden; sonst ein Standard-Quadrat mit dem
     Raumtyp als Label)
  4. Summen — Anzahl Räume, Σ Boden / Decke / Wand brutto / Wand
     netto, Σ Volumen
  5. Footer — "Erstellt mit BauLV", Hinweis Vorabkalkulation, Seite
     X von Y

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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
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
    """German-style number with comma as decimal separator. None → em-dash."""
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
    translates that into a 404. Empty projects (no rooms) get a
    placeholder PDF with the header + a "noch keine Räume erfasst"
    note. We don't 400 in that case because the user might want a
    cover sheet for a project they're about to populate.
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
        author="BauLV",
    )

    styles = _build_styles()
    story: list = []

    _append_header(story, project, creator, styles)

    if not rooms_with_context:
        story.append(Spacer(1, 6 * mm))
        story.append(
            Paragraph(
                "<b>Noch keine Räume erfasst.</b><br/>"
                "Laden Sie einen Bauplan hoch und starten Sie die "
                "KI-Plananalyse, oder erfassen Sie die Gebäudestruktur "
                "manuell unter <i>Gebäudestruktur</i>.",
                styles["MEMeta"],
            )
        )
    else:
        _append_overview_table(story, rooms_with_context, styles)
        _append_room_details(story, rooms_with_context, styles)
        _append_summary(story, rooms_with_context, styles)

    doc.build(
        story,
        onFirstPage=_draw_footer,
        onLaterPages=_draw_footer,
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
    return base


# ---------------------------------------------------------------------------
# Header — Projekt-Metadaten + Plan-Referenzen + Ersteller/Datum
# ---------------------------------------------------------------------------


def _append_header(
    story: list,
    project: Project,
    creator: User | None,
    styles,
) -> None:
    story.append(Paragraph("MENGENERMITTLUNG", styles["METitle"]))
    story.append(
        Paragraph(
            "Vorabkalkulation nach branchenüblichen Mengenermittlungs-Standards",
            styles["MESubtitle"],
        )
    )

    # --- Projekt + Erstellungs-Metadaten ---
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    creator_label = (
        f"{creator.full_name} ({creator.email})"
        if creator is not None
        else "—"
    )
    rows: list[tuple[str, str]] = [
        ("Projekt", project.name or "—"),
        ("Adresse", project.address or "—"),
        ("Auftraggeber", project.client_name or "—"),
        ("Projektnummer", project.project_number or "—"),
        ("Grundstücksnr.", project.grundstuecksnr or "—"),
        ("Planverfasser", project.planverfasser or "—"),
        ("Erstellt am", today),
        ("Erstellt von", creator_label),
    ]
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

    # --- Plan-Referenzen (falls vorhanden) ---
    plans = list(project.plans or [])
    if plans:
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
# Footer
# ---------------------------------------------------------------------------


def _draw_footer(canvas, doc) -> None:
    """Page-footer drawn on every page. Three-column layout: software
    identifier (left), Vorabkalkulations-Disclaimer (centre), page
    counter (right)."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#6b7280"))

    width = doc.pagesize[0]
    bottom_y = 12 * mm

    canvas.drawString(18 * mm, bottom_y, "Erstellt mit BauLV")
    canvas.drawCentredString(
        width / 2,
        bottom_y,
        "Vorabkalkulation — keine rechtsverbindliche Mengenermittlung",
    )
    page_num = canvas.getPageNumber()
    canvas.drawRightString(width - 18 * mm, bottom_y, f"Seite {page_num}")
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
