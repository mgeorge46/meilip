"""Employee-only dashboard views over NotificationDelivery rows."""
from django.views.generic import DetailView

from accounts.permissions import RoleRequiredMixin
from core.mixins import PaginatedListView

from .models import NotificationDelivery

STAFF_ROLES = (
    "ADMIN", "SUPER_ADMIN", "FINANCE", "ACCOUNT_MANAGER", "OPERATIONS",
)


class NotificationDeliveryList(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = NotificationDelivery
    template_name = "notifications/delivery_list.html"
    context_object_name = "rows"

    def get_queryset(self):
        qs = super().get_queryset().select_related("tenant", "landlord")
        g = self.request.GET
        if g.get("status"):
            qs = qs.filter(status=g["status"])
        if g.get("channel"):
            qs = qs.filter(channel=g["channel"])
        if g.get("template"):
            qs = qs.filter(template=g["template"])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_status"] = self.request.GET.get("status", "")
        ctx["active_channel"] = self.request.GET.get("channel", "")
        ctx["active_template"] = self.request.GET.get("template", "")
        return ctx


class NotificationDeliveryDetail(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = NotificationDelivery
    template_name = "notifications/delivery_detail.html"
    context_object_name = "row"
