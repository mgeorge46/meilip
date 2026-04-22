"""Scoring views — score leaderboard (employee-only) and tenant-score card.

Score is **never** rendered on the tenant or landlord portal surface.
"""
from django.db.models import Q

from accounts.permissions import RoleRequiredMixin
from core.mixins import PaginatedListView

from .models import TenantScore
from .tiers import Tier, TIER_COLOURS


STAFF_ROLES = (
    "SUPER_ADMIN", "ADMIN", "ACCOUNT_MANAGER",
    "COLLECTIONS", "SALES_REP", "FINANCE",
)


class TenantScoreListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = TenantScore
    template_name = "scoring/score_list.html"
    context_object_name = "scores"

    def get_queryset(self):
        qs = (
            TenantScore.objects.select_related("tenant")
            .order_by("-score", "tenant__full_name")
        )
        tier = self.request.GET.get("tier")
        if tier:
            qs = qs.filter(tier=tier)
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(
                Q(tenant__full_name__icontains=q)
                | Q(tenant__phone__icontains=q)
                | Q(tenant__email__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tiers"] = Tier.choices
        ctx["tier_colours"] = TIER_COLOURS
        ctx["active_tier"] = self.request.GET.get("tier", "")
        ctx["q"] = self.request.GET.get("q", "")
        return ctx
