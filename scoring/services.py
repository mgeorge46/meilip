"""Tenant scoring logic.

Score composition (0-100, higher = better):

    on_time_rate       35 pts   — share of invoices paid by due_date
    arrears_health     25 pts   — scaled by current outstanding / total billed
    overdue_penalty    15 pts   — penalised by count & age of OVERDUE invoices
    tenure_bonus       10 pts   — months as active tenant (capped)
    consistency        15 pts   — low volatility in payment cadence

Multi-house handling: per-tenancy sub-scores are weighted by total-billed
for that tenancy, so the score is economically weighted (a tenant with
2M outstanding on one house and a perfect record on a 50k house will
skew toward the 2M house's history).

Tenant with no invoices yet: returns a neutral 60 (Silver) so the roster
doesn't punish brand-new tenancies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from billing.models import Invoice, Payment, PaymentAllocation
from core.models import Tenant, TenantHouse

from .models import TenantScore
from .tiers import tier_for_score


NEUTRAL_SCORE_NEW_TENANT = 60


# Weights sum to 100
W_ON_TIME = 35
W_ARREARS = 25
W_OVERDUE = 15
W_TENURE = 10
W_CONSISTENCY = 15


@dataclass
class TenancyStats:
    tenancy_id: int
    total_billed: Decimal = Decimal("0")
    total_paid: Decimal = Decimal("0")
    outstanding: Decimal = Decimal("0")
    invoices_total: int = 0
    invoices_on_time: int = 0
    invoices_overdue: int = 0
    days_overdue_sum: int = 0
    tenure_months: int = 0
    # For consistency — list of days-late per paid invoice
    days_late_samples: List[int] = field(default_factory=list)


def _collect_tenancy_stats(tenancy: TenantHouse, *, today) -> TenancyStats:
    """Aggregate invoice-level stats for one tenancy."""
    stats = TenancyStats(tenancy_id=tenancy.pk)

    # Tenure — months from move_in_date (or billing_start_date) to today.
    anchor = tenancy.move_in_date or tenancy.billing_start_date
    if anchor:
        months = (today.year - anchor.year) * 12 + (today.month - anchor.month)
        stats.tenure_months = max(0, months)

    invoices = list(
        tenancy.invoices.exclude(
            status__in=[Invoice.Status.DRAFT, Invoice.Status.CANCELLED,
                        Invoice.Status.VOIDED]
        )
    )
    if not invoices:
        return stats

    for inv in invoices:
        stats.invoices_total += 1
        stats.total_billed += inv.total or Decimal("0")
        paid = inv.amount_paid
        stats.total_paid += paid
        stats.outstanding += max(Decimal("0"), inv.outstanding)

        if inv.status == Invoice.Status.PAID:
            # Use the latest approved allocation to determine pay date.
            last_alloc = (
                inv.allocations.filter(
                    payment__approval_status__in=["APPROVED", "AUTO_APPROVED"],
                )
                .order_by("-applied_at", "-allocated_at")
                .first()
            )
            pay_date = (
                (last_alloc.applied_at.date() if last_alloc and last_alloc.applied_at
                 else last_alloc.allocated_at.date() if last_alloc
                 else inv.due_date)
            )
            days_late = (pay_date - inv.due_date).days
            stats.days_late_samples.append(days_late)
            if days_late <= 0:
                stats.invoices_on_time += 1
        elif inv.status == Invoice.Status.OVERDUE:
            stats.invoices_overdue += 1
            stats.days_overdue_sum += max(0, (today - inv.due_date).days)

    return stats


def _on_time_pts(stats: TenancyStats) -> float:
    if stats.invoices_total == 0:
        return W_ON_TIME  # new tenant — give full benefit of doubt
    rate = stats.invoices_on_time / max(1, stats.invoices_total)
    return rate * W_ON_TIME


def _arrears_pts(stats: TenancyStats) -> float:
    if stats.total_billed <= 0:
        return W_ARREARS
    health = 1 - min(Decimal("1"), stats.outstanding / stats.total_billed)
    return float(health) * W_ARREARS


def _overdue_pts(stats: TenancyStats) -> float:
    if stats.invoices_overdue == 0:
        return W_OVERDUE
    # Each overdue invoice worth -5 pts, plus -1 per 7 days overdue (cap at 15).
    penalty = stats.invoices_overdue * 5
    penalty += stats.days_overdue_sum // 7
    return max(0, W_OVERDUE - penalty)


def _tenure_pts(stats: TenancyStats) -> float:
    # 0 months -> 0 pts, 24+ months -> full 10 pts (linear in between).
    if stats.tenure_months >= 24:
        return W_TENURE
    return (stats.tenure_months / 24) * W_TENURE


def _consistency_pts(stats: TenancyStats) -> float:
    samples = stats.days_late_samples
    if not samples:
        return W_CONSISTENCY
    # Std dev of days-late. Low std-dev = consistent payer.
    mean = sum(samples) / len(samples)
    var = sum((x - mean) ** 2 for x in samples) / len(samples)
    stdev = var ** 0.5
    # stdev of 0 -> full pts; stdev >= 14 days -> 0 pts.
    if stdev >= 14:
        return 0
    return (1 - stdev / 14) * W_CONSISTENCY


def _score_tenancy(stats: TenancyStats) -> Dict[str, float]:
    return {
        "on_time": _on_time_pts(stats),
        "arrears": _arrears_pts(stats),
        "overdue": _overdue_pts(stats),
        "tenure": _tenure_pts(stats),
        "consistency": _consistency_pts(stats),
    }


def _weighted_blend(per_tenancy: List[TenancyStats]) -> Dict[str, float]:
    """Blend per-tenancy sub-scores into a tenant-level score, weighted by
    each tenancy's total_billed. Falls back to equal weighting when nobody
    has been billed yet (all new tenants)."""
    total_billed = sum((t.total_billed for t in per_tenancy), Decimal("0"))
    blended = {"on_time": 0.0, "arrears": 0.0, "overdue": 0.0,
               "tenure": 0.0, "consistency": 0.0}
    if not per_tenancy:
        return blended
    if total_billed <= 0:
        # Equal weighting
        weight = 1 / len(per_tenancy)
        for t in per_tenancy:
            sub = _score_tenancy(t)
            for k, v in sub.items():
                blended[k] += v * weight
        return blended
    for t in per_tenancy:
        if t.total_billed <= 0:
            continue
        w = float(t.total_billed / total_billed)
        sub = _score_tenancy(t)
        for k, v in sub.items():
            blended[k] += v * w
    return blended


def calculate_score_for_tenant(tenant: Tenant, *, today=None, user=None) -> TenantScore:
    """Compute + persist the TenantScore row for one tenant."""
    today = today or timezone.localdate()
    tenancies = list(
        tenant.tenancies.exclude(status=TenantHouse.Status.PROSPECT)
    )
    per_stats = [
        _collect_tenancy_stats(th, today=today) for th in tenancies
    ]
    has_any_activity = any(t.invoices_total for t in per_stats)

    breakdown = _weighted_blend(per_stats)
    raw_score = sum(breakdown.values())
    if not has_any_activity:
        raw_score = NEUTRAL_SCORE_NEW_TENANT

    score = max(0, min(100, int(round(raw_score))))

    breakdown_payload = {
        "components": {k: round(v, 2) for k, v in breakdown.items()},
        "weights": {
            "on_time": W_ON_TIME,
            "arrears": W_ARREARS,
            "overdue": W_OVERDUE,
            "tenure": W_TENURE,
            "consistency": W_CONSISTENCY,
        },
        "tenancies": [
            {
                "tenancy_id": t.tenancy_id,
                "total_billed": str(t.total_billed),
                "total_paid": str(t.total_paid),
                "outstanding": str(t.outstanding),
                "invoices_total": t.invoices_total,
                "invoices_on_time": t.invoices_on_time,
                "invoices_overdue": t.invoices_overdue,
                "tenure_months": t.tenure_months,
            }
            for t in per_stats
        ],
        "new_tenant_neutral": not has_any_activity,
    }

    obj, _ = TenantScore.objects.update_or_create(
        tenant=tenant,
        defaults={
            "score": score,
            "tier": tier_for_score(score),
            "breakdown": breakdown_payload,
            "calculated_by": user,
        },
    )
    return obj


@transaction.atomic
def calculate_scores_for_all(*, today=None, user=None, tenant_ids=None) -> dict:
    """Recalculate scores for every non-deleted tenant (or a filtered subset).

    Returns summary `{"processed": int, "errors": [...]}`.
    """
    qs = Tenant.objects.all()
    if tenant_ids:
        qs = qs.filter(pk__in=tenant_ids)
    processed = 0
    errors = []
    for tenant in qs.iterator():
        try:
            calculate_score_for_tenant(tenant, today=today, user=user)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"tenant_id": tenant.pk, "error": str(exc)})
    return {"processed": processed, "errors": errors}
