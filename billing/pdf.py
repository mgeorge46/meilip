"""Reportlab-based PDF helpers for receipts and landlord statements.

Uses `reportlab` (no native deps — works on Windows dev + Linux prod) via the
high-level `platypus` flowables API for simple tabular documents.
"""
from decimal import Decimal
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)


BRAND_PRIMARY = colors.HexColor("#1B4F8A")
BRAND_MUTED = colors.HexColor("#6b7280")
BRAND_LIGHT = colors.HexColor("#f1f5f9")


def _base_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Brand", fontName="Helvetica-Bold", fontSize=16,
                              textColor=BRAND_PRIMARY, spaceAfter=2))
    styles.add(ParagraphStyle(name="Muted", fontName="Helvetica", fontSize=9,
                              textColor=BRAND_MUTED, spaceAfter=8))
    styles.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=12,
                              textColor=BRAND_PRIMARY, spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=10, leading=13))
    styles.add(ParagraphStyle(name="Small", fontName="Helvetica", fontSize=8, textColor=BRAND_MUTED))
    return styles


def _header(story, styles, title, subtitle):
    story.append(Paragraph("MEILI PROPERTY", styles["Brand"]))
    story.append(Paragraph(title, styles["H2"]))
    if subtitle:
        story.append(Paragraph(subtitle, styles["Muted"]))
    story.append(Spacer(1, 6))


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(BRAND_MUTED)
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
    canvas.drawString(15 * mm, 10 * mm, f"Generated {stamp}")
    canvas.drawRightString(
        doc.pagesize[0] - 15 * mm, 10 * mm, f"Page {canvas.getPageNumber()}"
    )
    canvas.restoreState()


def _build(title, story_callable, filename):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title=title,
    )
    styles = _base_styles()
    story = []
    story_callable(story, styles)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Receipt PDF
# ---------------------------------------------------------------------------
def receipt_pdf(receipt):
    """Render a single Receipt as a one-page PDF."""
    payment = receipt.payment
    refund = receipt.refund
    tenant = (payment.tenant if payment else None) or (getattr(refund, "tenant", None) if refund else None)
    bank = payment.bank_account if payment else None

    def story_cb(story, styles):
        title = f"Receipt {receipt.number}"
        subtitle = f"{receipt.get_kind_display()} receipt · Issued {timezone.localtime(receipt.issued_at).strftime('%Y-%m-%d %H:%M')}"
        _header(story, styles, title, subtitle)

        # Party block
        if tenant:
            story.append(Paragraph(f"<b>Received from:</b> {tenant.full_name}", styles["Body"]))
            if tenant.phone:
                story.append(Paragraph(f"Phone: {tenant.phone}", styles["Small"]))
            if tenant.email:
                story.append(Paragraph(f"Email: {tenant.email}", styles["Small"]))
        story.append(Spacer(1, 8))

        # Details table
        rows = [
            ["Receipt number", receipt.number or "—"],
            ["Kind", receipt.get_kind_display()],
            ["Amount (UGX)", f"{receipt.amount:,}" if receipt.amount is not None else "—"],
            ["Issued", timezone.localtime(receipt.issued_at).strftime("%Y-%m-%d %H:%M")],
        ]
        if payment:
            rows.extend([
                ["Payment number", payment.number or "—"],
                ["Payment method", payment.get_method_display()],
                ["Bank / provider", bank.name if bank else "—"],
                ["External reference", payment.reference_number or "—"],
            ])
        if refund:
            rows.extend([
                ["Refund number", refund.number or "—"],
                ["Refund method", refund.get_method_display()],
            ])

        t = Table(rows, colWidths=[55 * mm, None])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), BRAND_MUTED),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, BRAND_LIGHT]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(t)

        story.append(Spacer(1, 12))
        story.append(Paragraph(
            "This receipt is system-generated and confirms the amount shown above has been received/refunded. "
            "For queries, contact Meili Property finance.",
            styles["Small"],
        ))

    return _build(
        title=f"Receipt {receipt.number}",
        story_callable=story_cb,
        filename=f"receipt_{receipt.number or receipt.pk}",
    )


# ---------------------------------------------------------------------------
# Landlord statement PDF
# ---------------------------------------------------------------------------
def landlord_statement_pdf(landlord, invoices, houses):
    """Render a landlord monthly/running statement."""
    def story_cb(story, styles):
        _header(
            story, styles,
            f"Landlord statement — {landlord.full_name}",
            f"{'Meili-owned' if landlord.is_meili_owned else 'Managed'} · generated {timezone.localtime().strftime('%Y-%m-%d')}",
        )

        # Contact
        story.append(Paragraph("<b>Contact</b>", styles["H2"]))
        contact_rows = [
            ["Phone", landlord.phone or "—"],
            ["Email", landlord.email or "—"],
            ["Bank", f"{landlord.bank_name or '—'} · {landlord.bank_account_number or '—'}"],
        ]
        t = Table(contact_rows, colWidths=[35 * mm, None])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1), BRAND_MUTED),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)

        # Houses
        story.append(Paragraph("<b>Portfolio</b>", styles["H2"]))
        house_rows = [["Estate", "House", "Rent (UGX)", "Occupancy"]]
        for h in houses:
            house_rows.append([
                h.estate.name if h.estate_id else "—",
                str(h),
                f"{h.periodic_rent:,}" if h.periodic_rent else "—",
                h.get_occupancy_status_display() if hasattr(h, "get_occupancy_status_display") else "—",
            ])
        if len(house_rows) == 1:
            house_rows.append(["—", "No houses under this landlord", "—", "—"])
        t2 = Table(house_rows, colWidths=[50 * mm, 55 * mm, 35 * mm, 35 * mm])
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t2)
        story.append(Spacer(1, 8))

        # Invoices
        story.append(Paragraph("<b>Invoices (latest 100)</b>", styles["H2"]))
        inv_rows = [["Number", "Period", "Issued", "Due", "Status", "Total (UGX)"]]
        total = Decimal("0")
        for inv in invoices:
            inv_rows.append([
                inv.number or f"draft-{inv.pk}",
                f"{inv.period_from.strftime('%Y-%m-%d')} → {inv.period_to.strftime('%Y-%m-%d')}",
                inv.issue_date.strftime("%Y-%m-%d"),
                inv.due_date.strftime("%Y-%m-%d"),
                inv.get_status_display(),
                f"{inv.total:,}" if inv.total else "0",
            ])
            total += (inv.total or Decimal("0"))
        if len(inv_rows) == 1:
            inv_rows.append(["—", "No invoices", "—", "—", "—", "—"])
        else:
            inv_rows.append(["", "", "", "", "TOTAL", f"{total:,}"])

        t3 = Table(inv_rows, colWidths=[28 * mm, 45 * mm, 25 * mm, 25 * mm, 25 * mm, 30 * mm])
        t3.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, BRAND_LIGHT]),
            ("ALIGN", (5, 1), (5, -1), "RIGHT"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.5, BRAND_PRIMARY),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t3)

        story.append(Spacer(1, 10))
        story.append(Paragraph(
            "Held tenant advance balances are not shown — per SPEC §20.2 the landlord statement "
            "reflects only recognised / current-period activity.",
            styles["Small"],
        ))

    return _build(
        title=f"Landlord statement — {landlord.full_name}",
        story_callable=story_cb,
        filename=f"landlord_statement_{landlord.pk}_{timezone.localtime().strftime('%Y%m%d')}",
    )
