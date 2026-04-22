"""PDF export of Leistungsverzeichnis.

Pure-Python via reportlab — no system libraries required, works on
Railway's default image without adding apt packages or rebuilding the
container (weasyprint would need cairo/pango/gdk-pixbuf and is a fragile
dependency on minimal Linux base images).

The layout mirrors ``xlsx_exporter`` so Excel and PDF are interchangeable
for tender submissions:

  * A4 portrait, conservative margins
  * Header block with the project metadata the frontend already captures
    (name, address, client, project/plot number, Planverfasser)
  * One table per Leistungsgruppe: Pos.-Nr., Kurztext + optional
    Langtext sub-row, Einheit, Menge, EP, GP, and a group subtotal row
  * Grand total
  * DSGVO-conformant footer on every page: software identifier +
    generation timestamp + a note that the document contains only the
    project data the user entered

This is a Basis-tier feature — no ``require_feature`` gate. Only Excel
(``excel_export``) is Pro-gated. See ``app/subscriptions.py``.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe(text: object) -> str:
    """Escape text for reportlab Paragraph (XML-like markup parser).

    reportlab parses Paragraph content as mini-XML, so a single ``&`` or
    ``<`` in a user-provided project name or Langtext will crash the
    build. The xlsx path is cell-based and doesn't have this problem;
    this function exists solely to bridge that difference."""
    if text is None:
        return ""
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_money(value) -> str:
    """German-style € formatting: 1.234,56 €. Empty → em-dash."""
    if value is None:
        return "—"
    # Two-step swap via a placeholder to avoid '1,234,567.89' → '1.234.567,89'
    # cross-contamination.
    formatted = f"{float(value):,.2f}"
    return (
        formatted.replace(",", "\u0001").replace(".", ",").replace("\u0001", ".")
        + " €"
    )


def _fmt_menge(value) -> str:
    """German-style 3-decimal quantity: 12,345. Empty → em-dash."""
    if value is None:
        return "—"
    formatted = f"{float(value):,.3f}"
    return formatted.replace(",", "\u0001").replace(".", ",").replace("\u0001", ".")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def export_lv_pdf(lv_id: UUID, db: AsyncSession) -> bytes:
    """Render the LV as a PDF document. Returns raw bytes.

    Raises ``ValueError`` if the LV does not exist, matching the contract
    the xlsx exporter and the route handler already assume.
    """
    stmt = (
        select(Leistungsverzeichnis)
        .where(Leistungsverzeichnis.id == lv_id)
        .options(
            selectinload(Leistungsverzeichnis.gruppen).selectinload(
                Leistungsgruppe.positionen
            ),
            selectinload(Leistungsverzeichnis.project),
        )
    )
    result = await db.execute(stmt)
    lv = result.scalars().first()
    if not lv:
        raise ValueError(f"LV {lv_id} not found")

    buffer = io.BytesIO()
    project_name = lv.project.name if lv.project else ""
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=22 * mm,
        title=f"LV {lv.trade}" + (f" — {project_name}" if project_name else ""),
        author="BauLV",
    )

    styles = _build_styles()
    story: list = []

    _append_header(story, lv, styles)
    _append_positions(story, lv, styles)

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="LVTitle",
            parent=styles["Heading1"],
            fontSize=16,
            spaceAfter=6,
            textColor=colors.HexColor("#1f2937"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="LVMeta",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="LVGruppe",
            parent=styles["Heading3"],
            fontSize=11,
            spaceBefore=10,
            spaceAfter=4,
            textColor=colors.HexColor("#1e40af"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="LVKurztext",
            parent=styles["Normal"],
            fontSize=9,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="LVLangtext",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#4b5563"),
            leftIndent=4,
        )
    )
    return styles


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _append_header(story: list, lv: Leistungsverzeichnis, styles) -> None:
    story.append(Paragraph("LEISTUNGSVERZEICHNIS", styles["LVTitle"]))

    project = lv.project
    rows: list[tuple[str, str]] = []
    if project is not None:
        rows.extend(
            [
                ("Projekt", project.name or "—"),
                ("Adresse", project.address or "—"),
                ("Auftraggeber", project.client_name or "—"),
                ("Projektnummer", project.project_number or "—"),
                ("Grundstücksnr.", project.grundstuecksnr or "—"),
                ("Planverfasser", project.planverfasser or "—"),
            ]
        )
    rows.extend(
        [
            ("Gewerk", lv.trade or "—"),
            ("Status", lv.status or "—"),
        ]
    )

    meta_table = Table(
        [
            [
                Paragraph(f"<b>{_safe(label)}:</b>", styles["LVMeta"]),
                Paragraph(_safe(value), styles["LVMeta"]),
            ]
            for label, value in rows
        ],
        colWidths=[35 * mm, 135 * mm],
        hAlign="LEFT",
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 8 * mm))


# ---------------------------------------------------------------------------
# Positions + totals
# ---------------------------------------------------------------------------


# Column widths for every Leistungsgruppe table. Total ≈ 174 mm, fits in
# the A4 content area (174 mm = 210 − 2×18 mm margins).
_GRUPPE_COLS = [
    18 * mm,   # Pos.-Nr.
    78 * mm,   # Kurztext / Langtext
    14 * mm,   # Einheit
    20 * mm,   # Menge
    20 * mm,   # EP
    24 * mm,   # GP
]


def _append_positions(story: list, lv: Leistungsverzeichnis, styles) -> None:
    if not lv.gruppen:
        story.append(
            Paragraph(
                "<i>Keine Positionen erfasst. Bitte führen Sie zuerst die "
                "Berechnung durch oder legen Sie Positionen manuell an.</i>",
                styles["LVMeta"],
            )
        )
        return

    grand_total = 0.0

    for gruppe in sorted(lv.gruppen, key=lambda g: g.sort_order):
        story.append(
            Paragraph(
                f"LG {_safe(gruppe.nummer)} — {_safe(gruppe.bezeichnung)}",
                styles["LVGruppe"],
            )
        )

        rows: list[list] = [["Pos.-Nr.", "Kurztext", "Einh.", "Menge", "EP", "GP"]]
        group_total = 0.0
        langtext_row_indices: list[int] = []
        summary_row_index: int | None = None

        for pos in sorted(gruppe.positionen, key=lambda p: p.sort_order):
            gp_numeric = float(pos.gesamtpreis) if pos.gesamtpreis else None
            if gp_numeric is not None:
                group_total += gp_numeric
            rows.append(
                [
                    _safe(pos.positions_nummer),
                    Paragraph(_safe(pos.kurztext), styles["LVKurztext"]),
                    _safe(pos.einheit),
                    _fmt_menge(pos.menge),
                    _fmt_money(pos.einheitspreis),
                    _fmt_money(gp_numeric),
                ]
            )
            if pos.langtext:
                rows.append(
                    [
                        "",
                        Paragraph(_safe(pos.langtext), styles["LVLangtext"]),
                        "",
                        "",
                        "",
                        "",
                    ]
                )
                langtext_row_indices.append(len(rows) - 1)

        # Group subtotal row
        rows.append(
            [
                "",
                Paragraph("<b>Summe Leistungsgruppe</b>", styles["LVMeta"]),
                "",
                "",
                "",
                _fmt_money(group_total),
            ]
        )
        summary_row_index = len(rows) - 1
        grand_total += group_total

        style_cmds: list = [
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0e7ff")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#4b5563")),
            # Body defaults
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            # Subtotal row
            ("LINEABOVE", (0, summary_row_index), (-1, summary_row_index),
             0.5, colors.HexColor("#4b5563")),
            ("FONTNAME", (0, summary_row_index), (-1, summary_row_index),
             "Helvetica-Bold"),
        ]
        # Shade langtext rows lightly so they read as sub-rows, not new
        # positions.
        for idx in langtext_row_indices:
            style_cmds.append(
                ("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#f9fafb"))
            )

        t = Table(rows, colWidths=_GRUPPE_COLS, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle(style_cmds))
        story.append(t)
        story.append(Spacer(1, 4 * mm))

    # Grand total ------------------------------------------------------------
    total_table = Table(
        [["Gesamtsumme", _fmt_money(grand_total)]],
        colWidths=[150 * mm, 24 * mm],
        hAlign="LEFT",
    )
    total_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(Spacer(1, 6 * mm))
    story.append(total_table)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def _draw_footer(canvas, document) -> None:
    """DSGVO-conformant page footer — software identifier, generation
    timestamp, and a data-minimization note. Called by reportlab once per
    page after the main flow lays out."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#6b7280"))

    footer_y = 10 * mm
    canvas.drawString(
        18 * mm,
        footer_y + 4 * mm,
        "Generiert mit BauLV — AI-gestützte Bau-Ausschreibungssoftware.",
    )
    canvas.drawString(
        18 * mm,
        footer_y,
        "Enthält ausschließlich die im Leistungsverzeichnis erfassten "
        "Projektangaben (DSGVO-konforme Verarbeitung).",
    )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    canvas.drawRightString(
        document.pagesize[0] - 18 * mm,
        footer_y + 4 * mm,
        f"Seite {canvas.getPageNumber()} — erstellt {ts}",
    )
    canvas.restoreState()
