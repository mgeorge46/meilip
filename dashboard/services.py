"""Dashboard KPIs — compiled against the live ledger.

Every function returns plain-data (dict/list) so the template + chart
layer stay dumb. All money is in UGX.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

def _today():
    return timezone.localdate()


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


# ---------------------------------------------------------------------------
# Stat cards
# ---------------------------------------------------------------------------
def stat_cards(today=None) -> list[dict]:
    """Top-of-dashboard KPI cards.

    Returns a list so the template can iterate with consistent markup.
    """
    from accounting.models import Account
    from accounting.utils import SYS_AR_TENANTS, get_account
    from billing.models import Invoice, Payment
    from core.models import House, Tenant, TenantHouse

    today = today or _today()
    month_start = _month_start(today)

    # AR outstanding — invoices still in ISSUED/PARTIALLY_PAID/OVERDUE
    ar_outstanding = Decimal("0")
    for inv in Invoice.objects.filter(status__in=[
        Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE,
    ]).only("total", "id"):
        ar_outstanding += inv.outstanding

    # Billed this month
    billed = Invoice.objects.filter(
        issue_date__gte=month_start, issue_date__lt=_add_months(month_start, 1),
        status__in=[
            Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
            Invoice.Status.PAID, Invoice.Status.OVERDUE,
        ],
    ).aggregate(s=Sum("total"))["s"] or Decimal("0")

    # Collected this month (sum of allocations applied in the window)
    from billing.models import PaymentAllocation
    collected = PaymentAllocation.objects.filter(
        is_advance_hold=False,
        applied_at__gte=month_start, applied_at__lt=_add_months(month_start, 1),
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    # Collection rate. Cascade through windows so the rate is informative
    # even early in the month: current-month → trailing-30d → trailing-90d.
    def _rate_for(window_days):
        ws = today - timedelta(days=window_days)
        b = Invoice.objects.filter(
            issue_date__gte=ws,
            status__in=[
                Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.PAID, Invoice.Status.OVERDUE,
            ],
        ).aggregate(s=Sum("total"))["s"] or Decimal("0")
        c = PaymentAllocation.objects.filter(
            is_advance_hold=False, applied_at__gte=ws,
        ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        return (float(c * 100 / b), b) if b > 0 else (None, b)

    if billed > 0:
        collection_rate = float(collected * 100 / billed)
        rate_basis = "month"
    else:
        collection_rate, _ = _rate_for(30)
        rate_basis = "30d"
        if collection_rate is None:
            collection_rate, _ = _rate_for(90)
            rate_basis = "90d" if collection_rate is not None else None

    # Active occupancy %
    total_houses = House.objects.count()
    occupied = TenantHouse.objects.filter(
        status=TenantHouse.Status.ACTIVE,
    ).values("house_id").distinct().count()
    occupancy_pct = (occupied * 100.0 / total_houses) if total_houses else 0.0

    # Overdue invoices — derived: due_date < today AND outstanding > 0,
    # regardless of whether the status flag has been transitioned to
    # OVERDUE yet. This avoids the dashboard understating exposure when
    # the periodic "mark as overdue" task hasn't run.
    overdue_count = 0
    overdue_total = Decimal("0")
    for inv in (
        Invoice.objects.filter(
            due_date__lt=today,
            status__in=[
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.OVERDUE,
            ],
        ).only("total", "id", "due_date")
    ):
        out = inv.outstanding
        if out > 0:
            overdue_count += 1
            overdue_total += out

    active_tenants = Tenant.objects.filter(
        tenancies__status=TenantHouse.Status.ACTIVE,
    ).distinct().count()

    return [
        {
            "label": "Outstanding AR",
            "value": int(ar_outstanding),
            "format": "ugx",
            "icon": "bi-receipt",
            "tone": "warning" if ar_outstanding > 0 else "teal",
            "href": "/billing/invoices/?status=ISSUED",
        },
        {
            "label": "Billed this month",
            "value": int(billed),
            "format": "ugx",
            "icon": "bi-file-earmark-text",
            "tone": "info",
            "href": "/billing/invoices/",
        },
        {
            "label": "Collected this month",
            "value": int(collected),
            "format": "ugx",
            "icon": "bi-cash-coin",
            "tone": "success",
            "href": "/billing/payments/",
        },
        {
            "label": (
                "Collection rate (30d)" if rate_basis == "30d"
                else "Collection rate (90d)" if rate_basis == "90d"
                else "Collection rate"
            ),
            "value": (round(collection_rate, 1) if collection_rate is not None else None),
            "format": "pct",
            "icon": "bi-graph-up-arrow",
            "tone": (
                "neutral" if collection_rate is None
                else "success" if collection_rate >= 80
                else "warning" if collection_rate >= 50
                else "danger"
            ),
            "href": "/core/reports/collections-performance/",
        },
        {
            "label": "Occupancy",
            "value": round(occupancy_pct, 1),
            "format": "pct",
            "icon": "bi-house-check",
            "tone": "success" if occupancy_pct >= 85 else "warning",
            "href": None,
        },
        {
            "label": "Active tenants",
            "value": active_tenants,
            "format": "int",
            "icon": "bi-people",
            "tone": "purple",
            "href": "/core/tenants/",
        },
        {
            "label": "Overdue invoices",
            "value": overdue_count,
            "format": "int",
            "icon": "bi-exclamation-triangle",
            "tone": "danger" if overdue_count else "success",
            "sub_value": int(overdue_total),
            "sub_format": "ugx",
            "href": "/billing/invoices/",
        },
    ]


# ---------------------------------------------------------------------------
# AR ageing buckets
# ---------------------------------------------------------------------------
def ar_ageing(today=None) -> list[dict]:
    """Bucket outstanding AR by days-past-due."""
    from billing.models import Invoice

    today = today or _today()
    buckets = [
        ("Not yet due", lambda d: (today - d).days < 0),
        ("0–30", lambda d: 0 <= (today - d).days <= 30),
        ("31–60", lambda d: 31 <= (today - d).days <= 60),
        ("61–90", lambda d: 61 <= (today - d).days <= 90),
        ("90+", lambda d: (today - d).days > 90),
    ]
    totals = {label: Decimal("0") for label, _ in buckets}
    for inv in Invoice.objects.filter(status__in=[
        Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE,
    ]).only("due_date", "total", "id"):
        out = inv.outstanding
        if out <= 0:
            continue
        for label, pred in buckets:
            if pred(inv.due_date):
                totals[label] += out
                break
    return [{"label": label, "amount": int(totals[label])} for label, _ in buckets]


# ---------------------------------------------------------------------------
# Revenue trend — last 12 months
# ---------------------------------------------------------------------------
def revenue_trend(today=None) -> list[dict]:
    from billing.models import Invoice, PaymentAllocation

    today = today or _today()
    current = _month_start(today)
    months = []
    for i in range(11, -1, -1):
        ms = _add_months(current, -i)
        me = _add_months(ms, 1)
        billed = Invoice.objects.filter(
            issue_date__gte=ms, issue_date__lt=me,
            status__in=[
                Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.PAID, Invoice.Status.OVERDUE,
            ],
        ).aggregate(s=Sum("total"))["s"] or Decimal("0")
        collected = PaymentAllocation.objects.filter(
            is_advance_hold=False, applied_at__gte=ms, applied_at__lt=me,
        ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        months.append({
            "label": ms.strftime("%b %y"),
            "billed": int(billed),
            "collected": int(collected),
        })
    return months


# ---------------------------------------------------------------------------
# Notification health
# ---------------------------------------------------------------------------
def notification_health(today=None) -> dict:
    from notifications.models import DeliveryStatus, NotificationDelivery

    today = today or _today()
    window_start = today - timedelta(days=7)
    qs = NotificationDelivery.objects.filter(created_at__gte=window_start)
    total = qs.count()
    sent = qs.filter(status=DeliveryStatus.SENT).count()
    failed = qs.filter(status=DeliveryStatus.FAILED).count()
    queued = qs.filter(status__in=[DeliveryStatus.QUEUED, DeliveryStatus.SENDING]).count()
    return {
        "total": total,
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "success_rate": round(sent * 100.0 / total, 1) if total else None,
    }


# ---------------------------------------------------------------------------
# Top-N tables
# ---------------------------------------------------------------------------
def top_arrears(limit=5) -> list[dict]:
    from billing.models import Invoice

    rows = []
    seen = {}
    for inv in (
        Invoice.objects.filter(status__in=[
            Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
            Invoice.Status.OVERDUE,
        ])
        .select_related("tenant_house__tenant", "tenant_house__house")
    ):
        out = inv.outstanding
        if out <= 0:
            continue
        tid = inv.tenant_house.tenant_id
        row = seen.setdefault(tid, {
            "tenant": inv.tenant_house.tenant,
            "outstanding": Decimal("0"),
            "oldest_due": inv.due_date,
        })
        row["outstanding"] += out
        if inv.due_date < row["oldest_due"]:
            row["oldest_due"] = inv.due_date
    rows = sorted(seen.values(), key=lambda r: r["outstanding"], reverse=True)[:limit]
    return [
        {
            "tenant_name": r["tenant"].full_name,
            "tenant_id": r["tenant"].pk,
            "outstanding": int(r["outstanding"]),
            "oldest_due": r["oldest_due"],
        }
        for r in rows
    ]


def recent_payments(limit=5) -> list[dict]:
    from billing.models import Payment

    return [
        {
            "number": p.number or f"#{p.pk}",
            "tenant": p.tenant.full_name,
            "amount": int(p.amount),
            "received_at": p.received_at,
            "method": p.get_method_display(),
        }
        for p in Payment.objects.select_related("tenant").order_by("-received_at")[:limit]
    ]
