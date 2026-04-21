"""Portal views — tenant + landlord self-service.

Mixins enforce that the authenticated user owns the underlying profile. All
querysets filter server-side so a tenant cannot enumerate another tenant's
invoices by tweaking a URL, and likewise for landlords.
"""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView, TemplateView, View

from billing.models import Invoice, Payment, Receipt
from core.models import Estate, House, Landlord, Tenant, TenantHouse

from .models import LandlordStatement
from .services import (
    MAX_STATEMENT_MONTHS,
    StatementWindowError,
    build_statement_context,
    enforce_window,
    models_or,
)


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------
class TenantPortalMixin(LoginRequiredMixin):
    """Require the user to have a tenant_profile OneToOne to core.Tenant."""

    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        tenant = getattr(request.user, "tenant_profile", None)
        if tenant is None or tenant.is_deleted:
            raise PermissionDenied("This portal is available to tenants only.")
        request.tenant = tenant
        return super().dispatch(request, *args, **kwargs)


class LandlordPortalMixin(LoginRequiredMixin):
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        landlord = getattr(request.user, "landlord_profile", None)
        if landlord is None or landlord.is_deleted:
            raise PermissionDenied("This portal is available to landlords only.")
        request.landlord = landlord
        return super().dispatch(request, *args, **kwargs)


# ===========================================================================
# Tenant views
# ===========================================================================
class TenantDashboardView(TenantPortalMixin, TemplateView):
    template_name = "portal/tenant/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tenant = self.request.tenant
        tenancies = tenant.tenancies.select_related("house__estate").order_by("-created_at")
        invoices = (
            Invoice.objects.filter(tenant_house__tenant=tenant)
            .exclude(status__in=[Invoice.Status.CANCELLED, Invoice.Status.VOIDED])
            .order_by("-issue_date")[:10]
        )
        open_invoices = [inv for inv in invoices if inv.outstanding > 0]
        ctx.update({
            "tenant": tenant,
            "tenancies": tenancies,
            "invoices": invoices,
            "open_invoices": open_invoices,
        })
        return ctx


class TenantInvoiceListView(TenantPortalMixin, ListView):
    template_name = "portal/tenant/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 50

    def get_queryset(self):
        return (
            Invoice.objects.filter(tenant_house__tenant=self.request.tenant)
            .select_related("tenant_house__house__estate")
            .order_by("-issue_date", "-id")
        )


class TenantInvoiceDetailView(TenantPortalMixin, DetailView):
    template_name = "portal/tenant/invoice_detail.html"
    context_object_name = "invoice"

    def get_queryset(self):
        return (
            Invoice.objects.filter(tenant_house__tenant=self.request.tenant)
            .select_related("tenant_house__house__estate")
            .prefetch_related("lines", "tax_lines", "allocations__payment")
        )


class TenantPaymentListView(TenantPortalMixin, ListView):
    template_name = "portal/tenant/payment_list.html"
    context_object_name = "payments"
    paginate_by = 50

    def get_queryset(self):
        return (
            Payment.objects.filter(tenant=self.request.tenant)
            .order_by("-received_at", "-id")
        )


class TenantReceiptListView(TenantPortalMixin, ListView):
    template_name = "portal/tenant/receipt_list.html"
    context_object_name = "receipts"
    paginate_by = 50

    def get_queryset(self):
        return (
            Receipt.objects.filter(payment__tenant=self.request.tenant)
            .select_related("payment")
            .order_by("-issued_at", "-id")
        )


class TenantProfileView(TenantPortalMixin, View):
    template_name = "portal/tenant/profile.html"

    def get(self, request):
        return render(request, self.template_name, {"tenant": request.tenant})

    def post(self, request):
        tenant = request.tenant
        pn = request.POST.get("preferred_notification")
        pr = request.POST.get("preferred_receipt")
        changed = []
        if pn in Tenant.PreferredNotification.values and pn != tenant.preferred_notification:
            tenant.preferred_notification = pn
            changed.append("preferred_notification")
        if pr in Tenant.PreferredReceipt.values and pr != tenant.preferred_receipt:
            tenant.preferred_receipt = pr
            changed.append("preferred_receipt")
        if changed:
            tenant.save(update_fields=changed + ["updated_at"])
            messages.success(request, "Preferences updated.")
        return redirect("tenant:profile")


# ===========================================================================
# Landlord views
# ===========================================================================
class LandlordDashboardView(LandlordPortalMixin, TemplateView):
    template_name = "portal/landlord/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        landlord = self.request.landlord
        estates = Estate.objects.filter(landlord=landlord).order_by("name")
        houses = (
            House.objects.filter(models_or(Q_estate=estates, Q_direct=landlord))
            .select_related("estate")
            .order_by("estate__name", "house_number")
        )
        recent_statements = LandlordStatement.objects.filter(landlord=landlord)[:5]
        ctx.update({
            "landlord": landlord,
            "estates": estates,
            "houses": houses,
            "recent_statements": recent_statements,
            "max_statement_months": MAX_STATEMENT_MONTHS,
        })
        return ctx


class LandlordHouseListView(LandlordPortalMixin, TemplateView):
    template_name = "portal/landlord/house_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        landlord = self.request.landlord
        estates = Estate.objects.filter(landlord=landlord)
        houses = (
            House.objects.filter(models_or(Q_estate=estates, Q_direct=landlord))
            .select_related("estate")
            .prefetch_related("tenancies__tenant")
            .order_by("estate__name", "house_number")
        )
        ctx.update({"landlord": landlord, "houses": houses})
        return ctx


class LandlordStatementRequestView(LandlordPortalMixin, View):
    template_name = "portal/landlord/statement_request.html"

    def get(self, request):
        return render(request, self.template_name, {
            "landlord": request.landlord,
            "max_statement_months": MAX_STATEMENT_MONTHS,
        })

    def post(self, request):
        from .tasks import generate_landlord_statement

        try:
            period_start = date.fromisoformat(request.POST.get("period_start", ""))
            period_end = date.fromisoformat(request.POST.get("period_end", ""))
        except ValueError:
            messages.error(request, "Please provide valid start and end dates.")
            return redirect("landlord:statement-request")

        try:
            enforce_window(period_start, period_end)
        except StatementWindowError as exc:
            messages.error(request, str(exc))
            return redirect("landlord:statement-request")

        generate_landlord_statement.delay(
            landlord_id=request.landlord.pk,
            period_start_iso=period_start.isoformat(),
            period_end_iso=period_end.isoformat(),
            requested_by_id=request.user.pk,
            deliver=True,
        )
        messages.success(
            request,
            "Statement is being generated. It will appear below and be "
            "delivered via your preferred channel once ready.",
        )
        return redirect("landlord:statement-list")


class LandlordStatementListView(LandlordPortalMixin, ListView):
    template_name = "portal/landlord/statement_list.html"
    context_object_name = "statements"
    paginate_by = 50

    def get_queryset(self):
        return LandlordStatement.objects.filter(landlord=self.request.landlord)


class LandlordStatementDownloadView(LandlordPortalMixin, View):
    def get(self, request, pk):
        stmt = get_object_or_404(
            LandlordStatement, pk=pk, landlord=request.landlord
        )
        if not stmt.pdf:
            raise Http404("Statement PDF is not yet available.")
        return FileResponse(
            stmt.pdf.open("rb"),
            as_attachment=True,
            filename=stmt.pdf.name.rsplit("/", 1)[-1],
            content_type="application/pdf",
        )


class LandlordProfileView(LandlordPortalMixin, View):
    template_name = "portal/landlord/profile.html"

    def get(self, request):
        return render(request, self.template_name, {"landlord": request.landlord})

    def post(self, request):
        landlord = request.landlord
        channel = request.POST.get("preferred_statement_channel")
        whatsapp = (request.POST.get("whatsapp_number") or "").strip()
        changed = []
        if channel in Landlord.StatementChannel.values and channel != landlord.preferred_statement_channel:
            landlord.preferred_statement_channel = channel
            changed.append("preferred_statement_channel")
        if whatsapp != landlord.whatsapp_number:
            landlord.whatsapp_number = whatsapp
            changed.append("whatsapp_number")
        if changed:
            landlord.save(update_fields=changed + ["updated_at"])
            messages.success(request, "Preferences updated.")
        return redirect("landlord:profile")
