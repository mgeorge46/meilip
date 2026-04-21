"""Portal services.

`build_statement_context(landlord, period_start, period_end)`
    - Aggregates invoices + allocations for the landlord's managed houses
    - Returns a dict with the same structure as `MARY NANTAYIRO Jan 2026 Report.pdf`
      and `Teddy.pdf`: houses rows (grouped by estate), defaulters rows, summary
      block, and landlord-payments list.
    - **NEVER touches held-advance accounts** — statements show only cost,
      paid, balance per tenancy. Held balances are fiduciary/deferred and not
      visible to landlords (SPEC §20).

`render_statement_pdf(context) -> bytes`
    - ReportLab Platypus rendering of the context.

Max query window enforced at the caller (view/task): period_end - period_start <= 6 months.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import List, Optional

from django.db.models import Sum
from django.utils import timezone

from accounting.models import JournalEntry
from billing.models import Invoice, Payment, PaymentAllocation


MAX_STATEMENT_MONTHS = 6


@dataclass
class HouseRow:
    unit: int
    tenant: str
    cost: int = 0
    amount_paid: int = 0
    balance: int = 0
    period: str = ""
    estate: str = ""


@dataclass
class DefaulterRow:
    unit: int
    tenant: str
    arrears: int = 0
    amount_paid: int = 0
    balance: int = 0
    period: str = ""


@dataclass
class LandlordPayment:
    date: date
    amount: int


@dataclass
class StatementContext:
    landlord_name: str
    report_date: date
    period_start: date
    period_end: date
    period_label: str
    rows: List[HouseRow] = field(default_factory=list)
    defaulters: List[DefaulterRow] = field(default_factory=list)
    landlord_payments: List[LandlordPayment] = field(default_factory=list)
    # Summary block
    amount_paid_for_period: int = 0
    arrears_cleared_by_defaulters: int = 0
    total_collection: int = 0
    commission: int = 0
    landlord_net: int = 0
    payments_to_landlord: int = 0
    closing_balance: int = 0


class StatementWindowError(ValueError):
    """Raised when requested period exceeds MAX_STATEMENT_MONTHS."""


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def enforce_window(period_start: date, period_end: date) -> None:
    if period_end < period_start:
        raise StatementWindowError("period_end must be on/after period_start.")
    if _months_between(period_start, period_end) + 1 > MAX_STATEMENT_MONTHS:
        raise StatementWindowError(
            f"Statement window capped at {MAX_STATEMENT_MONTHS} months."
        )


def build_statement_context(landlord, period_start: date, period_end: date) -> StatementContext:
    enforce_window(period_start, period_end)

    period_label = period_start.strftime("%b-%y")

    # All houses under the landlord (direct or via their estates)
    from core.models import House, Estate
    estates = Estate.objects.filter(landlord=landlord)
    houses = House.objects.filter(
        models_or(Q_estate=estates, Q_direct=landlord)
    ).select_related("estate").order_by("estate__name", "house_number", "id")

    rows: list[HouseRow] = []
    defaulters: list[DefaulterRow] = []
    total_cost = 0
    total_paid = 0

    unit_counter = 0
    for h in houses:
        unit_counter += 1
        invoices = (
            Invoice.objects.filter(
                tenant_house__house=h,
                period_from__lte=period_end,
                period_to__gte=period_start,
            )
            .exclude(status__in=[Invoice.Status.CANCELLED, Invoice.Status.VOIDED])
            .select_related("tenant_house__tenant")
            .order_by("period_from")
        )
        if not invoices:
            rows.append(HouseRow(
                unit=unit_counter,
                tenant="(vacant)",
                estate=h.estate.name if h.estate else "",
                period=period_label,
            ))
            continue

        for inv in invoices:
            cost = int(inv.total or 0)
            paid = int(inv.amount_paid or 0)
            balance = cost - paid
            total_cost += cost
            total_paid += paid
            rows.append(HouseRow(
                unit=unit_counter,
                tenant=inv.tenant_house.tenant.full_name if inv.tenant_house and inv.tenant_house.tenant else "",
                cost=cost,
                amount_paid=paid,
                balance=balance,
                period=inv.period_from.strftime("%B-%y") if inv.period_from else period_label,
                estate=h.estate.name if h.estate else "",
            ))

    # Defaulters: invoices from periods BEFORE the statement period that still had balance
    cutoff = period_start
    arrear_invoices = (
        Invoice.objects.filter(
            tenant_house__house__in=houses,
            period_to__lt=cutoff,
        )
        .exclude(status__in=[Invoice.Status.CANCELLED, Invoice.Status.VOIDED, Invoice.Status.PAID])
        .select_related("tenant_house__tenant")
        .order_by("tenant_house__tenant__full_name", "period_from")
    )
    arrears_cleared = 0
    for i, inv in enumerate(arrear_invoices, start=1):
        cost = int(inv.total or 0)
        paid_in_window = int(
            PaymentAllocation.objects.filter(
                invoice=inv,
                payment__received_on__gte=period_start,
                payment__received_on__lte=period_end,
                payment__approval_status=Payment.ApprovalStatus.APPROVED,
            ).aggregate(s=Sum("amount"))["s"] or 0
        )
        balance = cost - int(inv.amount_paid or 0)
        defaulters.append(DefaulterRow(
            unit=i,
            tenant=inv.tenant_house.tenant.full_name if inv.tenant_house and inv.tenant_house.tenant else "",
            arrears=cost,
            amount_paid=paid_in_window,
            balance=balance,
            period=inv.period_from.strftime("%b-%y") if inv.period_from else "",
        ))
        arrears_cleared += paid_in_window

    # Landlord payments made during the window (JournalEntry debiting LANDLORD_PAYABLE)
    from accounting.utils import SYS_LANDLORD_PAYABLE, get_account
    try:
        landlord_payable = get_account(SYS_LANDLORD_PAYABLE)
        payouts_qs = JournalEntry.objects.filter(
            status=JournalEntry.Status.POSTED,
            entry_date__gte=period_start,
            entry_date__lte=period_end,
            lines__account=landlord_payable,
            lines__debit__gt=0,
            memo__icontains=landlord.full_name,
        ).distinct()
        payments = []
        for je in payouts_qs:
            amt = int(
                je.lines.filter(account=landlord_payable).aggregate(s=Sum("debit"))["s"] or 0
            )
            if amt:
                payments.append(LandlordPayment(date=je.entry_date, amount=amt))
    except Exception:
        payments = []

    payments_to_landlord = sum(p.amount for p in payments)

    # Commission: sum COMMISSION_INCOME credits during window, filtered by landlord via memo heuristic.
    # For managed landlords only (skip if meili-owned).
    commission = 0
    if not landlord.is_meili_owned:
        from accounting.utils import SYS_COMMISSION_INCOME
        try:
            commission_account = get_account(SYS_COMMISSION_INCOME)
            commission = int(
                JournalEntry.objects.filter(
                    status=JournalEntry.Status.POSTED,
                    entry_date__gte=period_start,
                    entry_date__lte=period_end,
                    lines__account=commission_account,
                    lines__credit__gt=0,
                    memo__icontains=landlord.full_name,
                ).aggregate(s=Sum("lines__credit"))["s"] or 0
            )
        except Exception:
            commission = 0

    total_collection = total_paid + arrears_cleared
    landlord_net = total_collection - commission
    closing_balance = landlord_net - payments_to_landlord

    return StatementContext(
        landlord_name=landlord.full_name,
        report_date=timezone.localdate(),
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
        rows=rows,
        defaulters=defaulters,
        landlord_payments=payments,
        amount_paid_for_period=total_paid,
        arrears_cleared_by_defaulters=arrears_cleared,
        total_collection=total_collection,
        commission=commission,
        landlord_net=landlord_net,
        payments_to_landlord=payments_to_landlord,
        closing_balance=closing_balance,
    )


# House query across direct-landlord + estate-landlord in one go.
from django.db.models import Q


def models_or(*, Q_estate, Q_direct):
    """Return a Q matching houses whose estate is in Q_estate OR whose
    direct `landlord` override is the landlord. Helper to keep the call site
    above tidy."""
    return Q(estate__in=Q_estate) | Q(landlord=Q_direct)


# ---------------------------------------------------------------------------
# PDF rendering — Platypus layout that mirrors the reference templates.
# ---------------------------------------------------------------------------
def render_statement_pdf(ctx: StatementContext) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Landlord Statement — {ctx.landlord_name} {ctx.period_label}",
    )
    styles = getSampleStyleSheet()
    h_title = ParagraphStyle(
        "title", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0F4C81"),
        alignment=1, spaceAfter=8,
    )
    muted = ParagraphStyle("muted", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#6c757d"))
    section_hdr = ParagraphStyle(
        "section", parent=styles["Heading3"], fontSize=11, textColor=colors.white,
        backColor=colors.HexColor("#0F4C81"), alignment=1, spaceBefore=6, spaceAfter=6,
    )

    elements = []

    # Header block mimicking reference (LandLord / name, Report Date / Period)
    elements.append(Paragraph("MEILI PROPERTY SOLUTIONS", h_title))
    hdr = Table(
        [
            ["LandLord", ctx.landlord_name, "Report Date :", ctx.report_date.strftime("%d %b %Y")],
            ["", "", "Period;", ctx.period_label],
        ],
        colWidths=[28 * mm, 75 * mm, 30 * mm, 35 * mm],
    )
    hdr.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6c757d")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#6c757d")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(hdr)
    elements.append(Spacer(1, 6))

    # Houses table (grouped by estate)
    data = [["HOUSE NO", "TENANT", "COST", "AMOUNT PD", "BALANCE", "PERIOD"]]
    current_estate = None
    estate_row_idxs = []
    for idx, r in enumerate(ctx.rows, start=1):
        if r.estate and r.estate != current_estate:
            data.append([r.estate.upper(), "", "", "", "", ""])
            estate_row_idxs.append(len(data) - 1)
            current_estate = r.estate
        data.append([
            str(r.unit),
            r.tenant,
            f"{r.cost:,}" if r.cost else "",
            f"{r.amount_paid:,}" if r.amount_paid else "",
            f"{r.balance:,}" if r.balance else ("0" if r.cost else ""),
            r.period,
        ])
    data.append([
        "TOTAL", "",
        f"{sum(r.cost for r in ctx.rows):,}",
        f"{sum(r.amount_paid for r in ctx.rows):,}",
        f"{sum(r.balance for r in ctx.rows):,}",
        "",
    ])

    houses_tbl = Table(
        data,
        colWidths=[20 * mm, 55 * mm, 25 * mm, 28 * mm, 25 * mm, 22 * mm],
        repeatRows=1,
    )
    style_cmds = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#adb5bd")),
        ("FONT", (0, 1), (-1, -2), "Helvetica", 9),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1f3f5")),
        ("ALIGN", (2, 0), (4, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in estate_row_idxs:
        style_cmds.append(("SPAN", (0, i), (-1, i)))
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#0F4C81")))
        style_cmds.append(("TEXTCOLOR", (0, i), (-1, i), colors.white))
        style_cmds.append(("FONT", (0, i), (-1, i), "Helvetica-Bold", 9))
        style_cmds.append(("ALIGN", (0, i), (-1, i), "CENTER"))
    houses_tbl.setStyle(TableStyle(style_cmds))
    elements.append(houses_tbl)
    elements.append(Spacer(1, 8))

    # Defaulters
    elements.append(Paragraph("DEFAULTERS", section_hdr))
    def_data = [["Unit", "TENANT", "ARREARS", "AMOUNT PD", "BALANCE", "PERIOD"]]
    for d in ctx.defaulters:
        def_data.append([
            str(d.unit), d.tenant,
            f"{d.arrears:,}" if d.arrears else "",
            f"{d.amount_paid:,}" if d.amount_paid else "",
            f"{d.balance:,}" if d.balance else "-",
            d.period,
        ])
    if not ctx.defaulters:
        for i in range(1, 4):
            def_data.append([str(i), "", "", "", "-", ""])
    def_data.append([
        "sub total", "",
        f"{sum(d.arrears for d in ctx.defaulters):,}",
        f"{sum(d.amount_paid for d in ctx.defaulters):,}",
        f"{sum(d.balance for d in ctx.defaulters):,}",
        "",
    ])
    def_tbl = Table(
        def_data,
        colWidths=[18 * mm, 55 * mm, 27 * mm, 27 * mm, 25 * mm, 23 * mm],
    )
    def_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#adb5bd")),
        ("FONT", (0, 1), (-1, -2), "Helvetica", 9),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1f3f5")),
        ("ALIGN", (2, 0), (4, -1), "RIGHT"),
    ]))
    elements.append(def_tbl)
    elements.append(Spacer(1, 12))

    # Summary + landlord payments side-by-side
    summary_data = [
        ["Summary", ""],
        ["Amount paid for period", f"{ctx.amount_paid_for_period:,}"],
        ["Arrears cleared by defaulters", f"{ctx.arrears_cleared_by_defaulters:,}"],
        ["Total Collection", f"{ctx.total_collection:,}"],
        ["Commission", f"{ctx.commission:,}"],
        ["LandLord's NET", f"{ctx.landlord_net:,}"],
        ["Payments to Landlord", f"{ctx.payments_to_landlord:,}"],
        ["closing balance", f"{ctx.closing_balance:,}"],
    ]
    summary_tbl = Table(summary_data, colWidths=[60 * mm, 30 * mm])
    summary_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("SPAN", (0, 0), (-1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#adb5bd")),
        ("FONT", (0, 3), (-1, 3), "Helvetica-Bold", 9),
        ("FONT", (0, 5), (-1, 5), "Helvetica-Bold", 9),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
    ]))

    pay_data = [["LandLord Payments", ""], ["DATE", "AMOUNT"]]
    for p in ctx.landlord_payments:
        pay_data.append([p.date.strftime("%d/%m/%Y"), f"{p.amount:,}"])
    while len(pay_data) < 6:
        pay_data.append(["", ""])
    pay_data.append(["Total", f"{sum(p.amount for p in ctx.landlord_payments):,}"])
    pay_tbl = Table(pay_data, colWidths=[45 * mm, 35 * mm])
    pay_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
        ("SPAN", (0, 0), (-1, 0)),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#adb5bd")),
        ("FONT", (0, 2), (-1, -2), "Helvetica", 9),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
    ]))

    # Side-by-side wrapper
    side_by_side = Table(
        [[summary_tbl, pay_tbl]],
        colWidths=[95 * mm, 85 * mm],
    )
    side_by_side.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(KeepTogether(side_by_side))

    doc.build(elements)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------
def persist_statement(landlord, period_start, period_end, pdf_bytes, ctx, *, requested_by=None):
    """Create or update a LandlordStatement row + save the PDF file."""
    from django.core.files.base import ContentFile
    from .models import LandlordStatement

    stmt, _ = LandlordStatement.objects.update_or_create(
        landlord=landlord,
        period_start=period_start,
        period_end=period_end,
        defaults={
            "status": LandlordStatement.Status.GENERATED,
            "generated_at": timezone.now(),
            "requested_by": requested_by,
            "total_cost": sum(r.cost for r in ctx.rows),
            "total_paid": ctx.amount_paid_for_period,
            "total_balance": sum(r.balance for r in ctx.rows),
            "commission_amount": ctx.commission,
            "landlord_net": ctx.landlord_net,
        },
    )
    fname = f"{landlord.pk}-{period_start:%Y%m}-{period_end:%Y%m}.pdf"
    stmt.pdf.save(fname, ContentFile(pdf_bytes), save=True)
    return stmt
