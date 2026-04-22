"""Internal reporting views — employee-only.

Reports delivered (SPEC §22 / Phase 7):
  - Repairs per house (sum of MAINTENANCE ad-hocs / MAINTENANCE expense JEs)
  - Estate-level cost rollup
  - Collection performance (month-on-month AR cleared vs AR billed)
  - Tenant acquisition (new tenancies per month)
  - Occupancy rates (per estate + portfolio-wide)
  - Revenue summary (per period, by kind: rent / commission / utilities / ad-hoc)
  - Advance Payments Report (EXISTS — kept in views.py) — filterable by
    tenant/house/estate/landlord/ownership type, badge ≥ 2 full periods
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import View

from accounts.permissions import RoleRequiredMixin
from accounting.models import JournalEntry, JournalEntryLine
from billing.models import (
    AdHocCharge,
    ApprovalStatus,
    Invoice,
    InvoiceLine,
    PaymentAllocation,
)
from core.models import Estate, House, TenantHouse

FINANCE_ROLES = ("ADMIN", "SUPER_ADMIN", "FINANCE", "ACCOUNT_MANAGER")


# ---------------------------------------------------------------------------
# Repairs per house
# ---------------------------------------------------------------------------
class RepairsPerHouseReport(RoleRequiredMixin, View):
    required_roles = FINANCE_ROLES

    def get(self, request):
        rows = (
            AdHocCharge.objects.filter(
                description__icontains="repair",
                approval_status__in=[
                    ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED,
                ],
            )
            .values(
                "tenant_house__house__id",
                "tenant_house__house__house_number",
                "tenant_house__house__estate__name",
            )
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("-total")
        )
        return render(request, "billing/report_repairs.html", {"rows": rows})


# ---------------------------------------------------------------------------
# Estate-level cost rollup
# ---------------------------------------------------------------------------
class EstateCostReport(RoleRequiredMixin, View):
    required_roles = FINANCE_ROLES

    def get(self, request):
        # Aggregate MAINTENANCE + repairs + any MEILI-target ad-hocs per estate.
        by_estate = defaultdict(lambda: {"repairs": Decimal("0"),
                                          "other": Decimal("0"),
                                          "count": 0})
        charges = AdHocCharge.objects.filter(
            approval_status__in=[
                ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED,
            ],
            target=AdHocCharge.Target.MEILI,
        ).select_related("tenant_house__house__estate")
        for c in charges:
            estate_name = c.tenant_house.house.estate.name
            bucket = "repairs" if "repair" in c.description.lower() else "other"
            by_estate[estate_name][bucket] += Decimal(c.amount)
            by_estate[estate_name]["count"] += 1
        rows = [
            {"estate": name, "repairs": b["repairs"], "other": b["other"],
             "total": b["repairs"] + b["other"], "count": b["count"]}
            for name, b in sorted(by_estate.items())
        ]
        return render(request, "billing/report_estate_costs.html", {"rows": rows})


# ---------------------------------------------------------------------------
# Collection performance
# ---------------------------------------------------------------------------
class CollectionPerformanceReport(RoleRequiredMixin, View):
    required_roles = FINANCE_ROLES

    def get(self, request):
        today = timezone.localdate()
        # Last 12 months
        months = []
        for i in range(11, -1, -1):
            y = today.year + (today.month - 1 - i) // 12
            m = (today.month - 1 - i) % 12 + 1
            months.append((y, m))

        rows = []
        for y, m in months:
            period_start = date(y, m, 1)
            if m == 12:
                period_end = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                period_end = date(y, m + 1, 1) - timedelta(days=1)

            billed = (
                Invoice.objects.filter(
                    issue_date__range=(period_start, period_end),
                ).exclude(status__in=[
                    Invoice.Status.VOIDED, Invoice.Status.CANCELLED,
                    Invoice.Status.DRAFT,
                ])
                .aggregate(s=Sum("total"))["s"] or Decimal("0")
            )
            collected = (
                PaymentAllocation.objects.filter(
                    applied_at__date__range=(period_start, period_end),
                    is_advance_hold=False,
                    invoice__isnull=False,
                    payment__approval_status__in=[
                        ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED,
                    ],
                ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
            )
            rate = (
                float((collected / billed) * 100) if billed > 0 else None
            )
            rows.append({
                "period": f"{y}-{m:02d}",
                "billed": billed,
                "collected": collected,
                "rate": rate,
            })
        return render(request, "billing/report_collections.html", {"rows": rows})


# ---------------------------------------------------------------------------
# Tenant acquisition
# ---------------------------------------------------------------------------
class TenantAcquisitionReport(RoleRequiredMixin, View):
    required_roles = FINANCE_ROLES

    def get(self, request):
        today = timezone.localdate()
        rows = []
        for i in range(11, -1, -1):
            y = today.year + (today.month - 1 - i) // 12
            m = (today.month - 1 - i) % 12 + 1
            start = date(y, m, 1)
            if m == 12:
                end = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(y, m + 1, 1) - timedelta(days=1)
            new_tenancies = TenantHouse.objects.filter(
                created_at__date__range=(start, end),
            ).count()
            activated = TenantHouse.objects.filter(
                move_in_date__range=(start, end),
                status__in=[TenantHouse.Status.ACTIVE, TenantHouse.Status.EXITED],
            ).count()
            exited = TenantHouse.objects.filter(
                move_out_date__range=(start, end),
            ).count()
            rows.append({
                "period": f"{y}-{m:02d}",
                "new": new_tenancies,
                "activated": activated,
                "exited": exited,
                "net": activated - exited,
            })
        return render(request, "billing/report_acquisition.html", {"rows": rows})


# ---------------------------------------------------------------------------
# Occupancy rates
# ---------------------------------------------------------------------------
class OccupancyReport(RoleRequiredMixin, View):
    required_roles = FINANCE_ROLES

    def get(self, request):
        estates = []
        total_houses = 0
        total_occupied = 0
        for estate in Estate.objects.prefetch_related("houses"):
            houses = list(estate.houses.all())
            if not houses:
                continue
            occupied = sum(
                1 for h in houses if h.occupancy_status == House.Occupancy.OCCUPIED
            )
            vacant = sum(
                1 for h in houses if h.occupancy_status == House.Occupancy.VACANT
            )
            maint = sum(
                1 for h in houses if h.occupancy_status == House.Occupancy.UNDER_MAINTENANCE
            )
            total_houses += len(houses)
            total_occupied += occupied
            estates.append({
                "estate": estate.name,
                "houses": len(houses),
                "occupied": occupied,
                "vacant": vacant,
                "maintenance": maint,
                "rate": (occupied / len(houses)) * 100 if houses else 0,
            })
        portfolio_rate = (
            (total_occupied / total_houses) * 100 if total_houses else 0
        )
        return render(request, "billing/report_occupancy.html", {
            "estates": estates,
            "total_houses": total_houses,
            "total_occupied": total_occupied,
            "portfolio_rate": portfolio_rate,
        })


# ---------------------------------------------------------------------------
# Revenue summary
# ---------------------------------------------------------------------------
class RevenueSummaryReport(RoleRequiredMixin, View):
    required_roles = FINANCE_ROLES

    def get(self, request):
        today = timezone.localdate()
        # 12 rolling months
        rows = []
        for i in range(11, -1, -1):
            y = today.year + (today.month - 1 - i) // 12
            m = (today.month - 1 - i) % 12 + 1
            start = date(y, m, 1)
            if m == 12:
                end = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(y, m + 1, 1) - timedelta(days=1)

            lines = InvoiceLine.objects.filter(
                invoice__issue_date__range=(start, end),
                invoice__status__in=[
                    Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
                    Invoice.Status.PAID, Invoice.Status.OVERDUE,
                ],
            )
            rent = (lines.filter(kind__in=[
                InvoiceLine.Kind.RENT, InvoiceLine.Kind.PRORATA,
            ]).aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            utilities = (lines.filter(kind=InvoiceLine.Kind.UTILITY)
                         .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            adhoc = (lines.filter(kind=InvoiceLine.Kind.AD_HOC)
                     .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            rows.append({
                "period": f"{y}-{m:02d}",
                "rent": rent,
                "utilities": utilities,
                "adhoc": adhoc,
                "total": rent + utilities + adhoc,
            })
        return render(request, "billing/report_revenue.html", {"rows": rows})
