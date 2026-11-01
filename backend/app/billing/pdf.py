"""Render a finalized (or draft preview) invoice as a PDF, using reportlab directly (no HTML
rendering step, no extra system dependency). Draft invoices get a "DRAFT / PROFORMA" watermark
line since their numbers can still change until finalized."""

from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

from app.billing.schemas import InvoiceOut

_STYLES = getSampleStyleSheet()
_TITLE = ParagraphStyle("InvoiceTitle", parent=_STYLES["Heading1"], fontSize=16, spaceAfter=2)
_SMALL = ParagraphStyle("Small", parent=_STYLES["Normal"], fontSize=9, leading=12)
_LABEL = ParagraphStyle("Label", parent=_STYLES["Normal"], fontSize=9, textColor=colors.grey)


def _rupees(paise: int) -> str:
    return f"Rs. {Decimal(paise) / 100:,.2f}"


def render_invoice_pdf(invoice: InvoiceOut) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
    )
    story: list[object] = []
    heading = "TAX INVOICE" if invoice.doc_type == "tax_invoice" else "PROFORMA INVOICE"
    if invoice.status == "draft":
        heading = f"{heading} (DRAFT - not final)"
    story.append(Paragraph(heading, _TITLE))
    story.append(
        Paragraph(
            f"Invoice no: {invoice.invoice_no or '(draft - not yet assigned)'} &nbsp;&nbsp; "
            f"Period: {invoice.period} &nbsp;&nbsp; "
            f"Date: {invoice.invoice_date or '-'} &nbsp;&nbsp; Due: {invoice.due_date or '-'}",
            _SMALL,
        )
    )
    story.append(Spacer(1, 6 * mm))

    seller_lines = [invoice.seller.name or "(seller not configured)", invoice.seller.address]
    if invoice.seller.gstin:
        seller_lines.append(f"GSTIN: {invoice.seller.gstin}")
    if invoice.seller.phone:
        seller_lines.append(invoice.seller.phone)
    buyer_lines = [invoice.buyer.name, invoice.buyer.address]
    if invoice.buyer.gstin:
        buyer_lines.append(f"GSTIN: {invoice.buyer.gstin}")

    party_table = Table(
        [
            [Paragraph("Seller", _LABEL), Paragraph("Billed to", _LABEL)],
            [
                Paragraph("<br/>".join(filter(None, seller_lines)), _SMALL),
                Paragraph("<br/>".join(filter(None, buyer_lines)), _SMALL),
            ],
        ],
        colWidths=[90 * mm, 90 * mm],
    )
    party_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(party_table)
    story.append(Spacer(1, 6 * mm))

    rows: list[list[object]] = [["#", "Description", "Qty", "Unit", "Amount"]]
    for line in invoice.lines:
        rows.append(
            [
                str(line.position),
                Paragraph(line.description, _SMALL),
                str(line.quantity),
                line.unit,
                _rupees(line.amount_paise),
            ]
        )
    lines_table = Table(rows, colWidths=[8 * mm, 92 * mm, 22 * mm, 22 * mm, 36 * mm])
    lines_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f2f7")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d7deea")),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(lines_table)
    story.append(Spacer(1, 4 * mm))

    totals = [
        ["Subtotal", _rupees(invoice.subtotal_paise)],
    ]
    if invoice.gst_kind == "intra":
        totals.append([f"CGST @ {invoice.gst_rate_percent / 2}%", _rupees(invoice.cgst_paise)])
        totals.append([f"SGST @ {invoice.gst_rate_percent / 2}%", _rupees(invoice.sgst_paise)])
    elif invoice.gst_kind == "inter":
        totals.append([f"IGST @ {invoice.gst_rate_percent}%", _rupees(invoice.igst_paise)])
    totals.append(["Total", _rupees(invoice.total_paise)])
    if invoice.paid_amount_paise > 0:
        totals.append(["Paid", _rupees(invoice.paid_amount_paise)])
        totals.append(["Balance due", _rupees(invoice.balance_due_paise)])
    totals_table = Table(totals, colWidths=[140 * mm, 40 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#17243b")),
            ]
        )
    )
    story.append(totals_table)
    story.append(Spacer(1, 8 * mm))

    if invoice.cancelled_at:
        story.append(Paragraph(f"CANCELLED: {invoice.cancelled_reason or '-'}", _SMALL))
    if invoice.seller.payment_details:
        story.append(Paragraph(f"Payment details: {invoice.seller.payment_details}", _SMALL))
    if invoice.seller.sac_code:
        story.append(Paragraph(f"SAC code: {invoice.seller.sac_code}", _SMALL))

    doc.build(story)
    return buffer.getvalue()
