"""Collections-performance service helpers (Phase F.2).

`compute_employee_month` returns the actual UGX collected for a given
employee in a given month, attribution rule:

    Sum of PaymentAllocation.amount where
        - allocation.applied_at falls within [month_start, month_start + 1 month)
        - allocation.is_advance_hold = False  (held advances aren't yet revenue)
        - the parent Payment is APPROVED or AUTO_APPROVED
        - the allocated invoice's tenant_house.house has this employee as the
          collections_person — checked via the effective setting (tenancy
          override -> house override -> estate override).

`compute_bonus(amount)` walks active brackets in min_amount order and returns
the first one whose [min_amount, max_amount] inclusive range contains
`amount`. Returns (bracket, bonus_amount) or (None, 0) if no bracket
matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from django.db.models import Q, Sum
from django.utils import timezone

from billing.models import ApprovalStatus, PaymentAllocation
from .models import CollectionsBonusBracket, CollectionsTarget, Employee


@dataclass
class CollectionsRow:
    employee: Employee
    month: date
    target: Decimal
    collected: Decimal
    bracket: Optional[CollectionsBonusBracket]
    bonus: Decimal

    @property
    def attainment_pct(self) -> Optional[float]:
        if not self.target:
            return None
        return float(self.collected) / float(self.target) * 100.0


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1, day=1)
    return d.replace(month=d.month + 1, day=1)


def compute_employee_month(
    employee: Employee, month: date,
    *, house=None, estate=None,
) -> Decimal:
    """UGX an employee collected during `month`.

    Optional `house` / `estate` further restrict to that scope (used by the
    report's filters).
    """
    ms = _month_start(month)
    me = _next_month(ms)
    qs = (
        PaymentAllocation.objects.filter(
            applied_at__gte=ms, applied_at__lt=me,
            is_advance_hold=False,
            payment__approval_status__in=[
                ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED,
            ],
        )
    )
    # Attribute via invoice -> tenant_house -> house.collections_person
    qs = qs.filter(invoice__tenant_house__house__collections_person=employee)
    if house is not None:
        qs = qs.filter(invoice__tenant_house__house=house)
    if estate is not None:
        qs = qs.filter(invoice__tenant_house__house__estate=estate)
    return qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")


def compute_bonus(amount: Decimal) -> tuple[Optional[CollectionsBonusBracket], Decimal]:
    """Return the matching active bracket + bonus amount for `collected`."""
    if amount is None or amount <= 0:
        return None, Decimal("0")
    candidates = CollectionsBonusBracket.objects.filter(is_active=True).order_by("min_amount")
    for b in candidates:
        if b.min_amount <= amount and (b.max_amount is None or amount <= b.max_amount):
            bonus = (amount * b.rate_percent / Decimal("100")).quantize(Decimal("1"))
            return b, bonus
    return None, Decimal("0")


def build_performance_rows(
    *, month: date,
    employees=None, house=None, estate=None,
) -> list[CollectionsRow]:
    """Build one row per employee for the given month.

    Includes any employee who has either (a) a target set for the month or
    (b) actually collected something. Filters cascade.
    """
    ms = _month_start(month)
    targets_by_emp = {
        t.employee_id: t.target_amount
        for t in CollectionsTarget.objects.filter(month=ms).select_related("employee")
    }
    if employees is None:
        # Anyone who is an account-manager / sales rep / collections person
        # somewhere in the org gets a row, plus anyone with a target.
        emp_ids = set(targets_by_emp.keys())
        from .models import House
        for h in House.objects.values_list("collections_person", flat=True):
            if h:
                emp_ids.add(h)
        employees = list(Employee.objects.filter(pk__in=emp_ids))

    rows = []
    for emp in employees:
        collected = compute_employee_month(emp, ms, house=house, estate=estate)
        target = targets_by_emp.get(emp.pk, Decimal("0"))
        if collected == 0 and not target:
            continue  # skip empty rows
        bracket, bonus = compute_bonus(collected)
        rows.append(CollectionsRow(
            employee=emp, month=ms, target=target,
            collected=collected, bracket=bracket, bonus=bonus,
        ))
    rows.sort(key=lambda r: r.collected, reverse=True)
    return rows
