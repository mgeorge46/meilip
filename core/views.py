"""Core CRUD views: Landlord, Estate, House, Tenant, TenantHouse, Employee, Supplier.

All views require authentication. Role restrictions per SPEC §16.9 (permission
matrix) are applied via RoleRequiredMixin.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, CreateView, UpdateView, View

from accounts.permissions import RoleRequiredMixin, has_any_role

from .forms import (
    EmployeeForm,
    EstateForm,
    HouseForm,
    LandlordForm,
    SupplierForm,
    TenantForm,
    TenantHouseForm,
    TenantMessageForm,
)
from .mixins import PaginatedListView
from .models import (
    Employee,
    Estate,
    House,
    Landlord,
    Supplier,
    Tenant,
    TenantHouse,
)
from .utils import get_effective_setting_with_source


# Role groups used throughout core CRUD
STAFF_ROLES = ("ADMIN", "SUPER_ADMIN", "ACCOUNT_MANAGER", "SALES_REP", "COLLECTIONS", "FINANCE")
ADMIN_ROLES = ("ADMIN", "SUPER_ADMIN")


# --- Landlord ---------------------------------------------------------------
class LandlordListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = Landlord
    template_name = "core/landlord_list.html"
    context_object_name = "landlords"

    def get_queryset(self):
        qs = super().get_queryset()
        q = (self.request.GET.get("q") or "").strip()
        status = self.request.GET.get("status") or ""
        owned = self.request.GET.get("owned") or ""
        if q:
            qs = qs.filter(
                Q(full_name__icontains=q) | Q(phone__icontains=q)
                | Q(email__icontains=q) | Q(id_number__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)
        if owned == "meili":
            qs = qs.filter(is_meili_owned=True)
        elif owned == "external":
            qs = qs.filter(is_meili_owned=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_q"] = self.request.GET.get("q", "")
        ctx["filter_status"] = self.request.GET.get("status", "")
        ctx["filter_owned"] = self.request.GET.get("owned", "")
        ctx["status_choices"] = Landlord.Status.choices
        return ctx


class LandlordDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = Landlord
    template_name = "core/landlord_detail.html"
    context_object_name = "landlord"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        from django.db.models import Q
        from billing.models import LandlordPayout
        from notifications.models import NotificationDelivery

        ctx = super().get_context_data(**kwargs)
        req = self.request
        allowed_sizes = [50, 100, 150, 250]

        ctx["estates"] = self.object.estates.all()
        ctx["houses"] = (
            House.objects.filter(estate__landlord=self.object)
            .select_related("estate")
            .order_by("estate__name", "house_number")
        )

        # ---- Payouts: search + pagination ----
        q_pay = (req.GET.get("pq") or "").strip()
        pay_qs = (
            LandlordPayout.objects.filter(landlord=self.object)
            .select_related("bank_account")
            .order_by("-paid_at", "-id")
        )
        if q_pay:
            qobj = Q(number__icontains=q_pay) | Q(reference_number__icontains=q_pay)
            try:
                from decimal import Decimal as _D
                qobj |= Q(amount=_D(q_pay.replace(",", "")))
            except Exception:
                pass
            pay_qs = pay_qs.filter(qobj)
        try:
            pay_size = int(req.GET.get("pps") or 50)
        except (TypeError, ValueError):
            pay_size = 50
        if pay_size not in allowed_sizes:
            pay_size = 50
        pay_paginator = Paginator(pay_qs, pay_size)
        try:
            pay_page_num = int(req.GET.get("pp") or 1)
        except (TypeError, ValueError):
            pay_page_num = 1
        pay_page = pay_paginator.get_page(pay_page_num)
        ctx.update({
            "payouts_page": pay_page,
            "payouts": pay_page.object_list,
            "payouts_total": pay_paginator.count,
            "payouts_q": q_pay,
            "payouts_size": pay_size,
            "payouts_sizes": allowed_sizes,
        })

        # ---- Messages: pagination ----
        notif_qs = (
            NotificationDelivery.objects.filter(landlord=self.object)
            .order_by("-created_at")
        )
        try:
            notif_size = int(req.GET.get("mps") or 50)
        except (TypeError, ValueError):
            notif_size = 50
        if notif_size not in allowed_sizes:
            notif_size = 50
        notif_paginator = Paginator(notif_qs, notif_size)
        try:
            notif_page_num = int(req.GET.get("mp") or 1)
        except (TypeError, ValueError):
            notif_page_num = 1
        notif_page = notif_paginator.get_page(notif_page_num)
        ctx.update({
            "notifications_page": notif_page,
            "notifications": notif_page.object_list,
            "notifications_total": notif_paginator.count,
            "notifications_size": notif_size,
            "notifications_sizes": allowed_sizes,
        })

        ctx["active_tab"] = req.GET.get("tab") or "overview"
        return ctx


class LandlordCreateView(RoleRequiredMixin, CreateView):
    required_roles = ADMIN_ROLES + ("ACCOUNT_MANAGER",)
    model = Landlord
    form_class = LandlordForm
    template_name = "core/landlord_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Landlord created.")
        return super().form_valid(form)


class LandlordUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ADMIN_ROLES + ("ACCOUNT_MANAGER",)
    model = Landlord
    form_class = LandlordForm
    template_name = "core/landlord_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Landlord updated.")
        return super().form_valid(form)


class LandlordDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES

    def post(self, request, pk):
        obj = get_object_or_404(Landlord, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "Landlord deleted.")
        return redirect("core:landlord-list")


# --- Estate -----------------------------------------------------------------
class EstateListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = Estate
    template_name = "core/estate_list.html"
    context_object_name = "estates"

    def get_queryset(self):
        qs = super().get_queryset().select_related("landlord")
        q = (self.request.GET.get("q") or "").strip()
        landlord_id = self.request.GET.get("landlord") or ""
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(location__icontains=q)
                | Q(landlord__full_name__icontains=q)
            )
        if landlord_id:
            qs = qs.filter(landlord_id=landlord_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_q"] = self.request.GET.get("q", "")
        ctx["filter_landlord"] = self.request.GET.get("landlord", "")
        ctx["landlords"] = Landlord.objects.order_by("full_name")
        return ctx


class EstateDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = Estate
    template_name = "core/estate_detail.html"
    context_object_name = "estate"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["houses"] = self.object.houses.all()
        return ctx


class EstateCreateView(RoleRequiredMixin, CreateView):
    required_roles = ADMIN_ROLES + ("ACCOUNT_MANAGER",)
    model = Estate
    form_class = EstateForm
    template_name = "core/estate_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Estate created.")
        return super().form_valid(form)


class EstateUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ADMIN_ROLES + ("ACCOUNT_MANAGER",)
    model = Estate
    form_class = EstateForm
    template_name = "core/estate_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Estate updated.")
        return super().form_valid(form)


class EstateDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES

    def post(self, request, pk):
        obj = get_object_or_404(Estate, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "Estate deleted.")
        return redirect("core:estate-list")


# --- House ------------------------------------------------------------------
class HouseListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = House
    template_name = "core/house_list.html"
    context_object_name = "houses"

    def get_queryset(self):
        qs = super().get_queryset().select_related("estate", "landlord", "estate__landlord")
        q = (self.request.GET.get("q") or "").strip()
        estate_id = self.request.GET.get("estate") or ""
        occupancy = self.request.GET.get("occupancy") or ""
        if q:
            qs = qs.filter(
                Q(house_number__icontains=q) | Q(name__icontains=q)
                | Q(estate__name__icontains=q)
            )
        if estate_id:
            qs = qs.filter(estate_id=estate_id)
        if occupancy:
            qs = qs.filter(occupancy_status=occupancy)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_q"] = self.request.GET.get("q", "")
        ctx["filter_estate"] = self.request.GET.get("estate", "")
        ctx["filter_occupancy"] = self.request.GET.get("occupancy", "")
        ctx["estates"] = Estate.objects.order_by("name")
        ctx["occupancy_choices"] = House.Occupancy.choices
        return ctx


SETTINGS_DISPLAY_FIELDS = [
    "currency", "billing_cycle", "billing_mode", "prorata_mode",
    "commission_type", "commission_scope", "commission_amount", "commission_percent",
    "tax_type", "security_deposit_policy", "initial_deposit_policy",
    "account_manager", "collections_person",
    "water_billed_separately", "garbage_billed_separately",
    "security_billed_separately", "electricity_billed_separately",
    "other_bills_billed_separately", "other_bills_description",
]


class HouseDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = House
    template_name = "core/house_detail.html"
    context_object_name = "house"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        effective = []
        for field in SETTINGS_DISPLAY_FIELDS:
            value, source = get_effective_setting_with_source(self.object, field)
            effective.append({"field": field.replace("_", " ").title(), "value": value, "source": source})
        ctx["effective_settings"] = effective
        ctx["tenancies"] = self.object.tenancies.select_related("tenant")
        return ctx


class HouseCreateView(RoleRequiredMixin, CreateView):
    required_roles = ADMIN_ROLES + ("ACCOUNT_MANAGER",)
    model = House
    form_class = HouseForm
    template_name = "core/house_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "House created.")
        return super().form_valid(form)


class HouseUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ADMIN_ROLES + ("ACCOUNT_MANAGER",)
    model = House
    form_class = HouseForm
    template_name = "core/house_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "House updated.")
        return super().form_valid(form)


class HouseDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES

    def post(self, request, pk):
        obj = get_object_or_404(House, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "House deleted.")
        return redirect("core:house-list")


# --- Tenant -----------------------------------------------------------------
class TenantListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = Tenant
    template_name = "core/tenant_list.html"
    context_object_name = "tenants"

    def get_queryset(self):
        qs = super().get_queryset().select_related("score", "sales_rep")
        q = (self.request.GET.get("q") or "").strip()
        tier = self.request.GET.get("tier")
        status = (self.request.GET.get("status") or "").upper()
        sales_rep_id = self.request.GET.get("sales_rep") or ""
        if q:
            qs = qs.filter(
                Q(full_name__icontains=q) | Q(phone__icontains=q)
                | Q(email__icontains=q) | Q(id_number__icontains=q)
            )
        if tier:
            qs = qs.filter(score__tier=tier)
        if sales_rep_id:
            qs = qs.filter(sales_rep_id=sales_rep_id)
        if status == "ACTIVE":
            qs = qs.filter(tenancies__status=TenantHouse.Status.ACTIVE).distinct()
        elif status == "PROSPECT":
            qs = qs.filter(tenancies__status=TenantHouse.Status.PROSPECT).exclude(
                tenancies__status=TenantHouse.Status.ACTIVE
            ).distinct()
        elif status == "EXITED":
            qs = qs.exclude(tenancies__status__in=[
                TenantHouse.Status.ACTIVE, TenantHouse.Status.PROSPECT,
            ]).distinct()
        sort = self.request.GET.get("sort", "")
        if sort == "score_desc":
            qs = qs.order_by("-score__score", "full_name")
        elif sort == "score_asc":
            qs = qs.order_by("score__score", "full_name")
        return qs

    def get_context_data(self, **kwargs):
        from scoring.tiers import Tier
        ctx = super().get_context_data(**kwargs)
        ctx["tiers"] = Tier.choices
        ctx["active_tier"] = self.request.GET.get("tier", "")
        ctx["active_sort"] = self.request.GET.get("sort", "")
        ctx["filter_q"] = self.request.GET.get("q", "")
        ctx["filter_status"] = self.request.GET.get("status", "")
        ctx["filter_sales_rep"] = self.request.GET.get("sales_rep", "")
        ctx["sales_reps"] = Employee.objects.filter(is_active=True).order_by("full_name")
        return ctx


class TenantDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = Tenant
    template_name = "core/tenant_detail.html"
    context_object_name = "tenant"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        from django.db.models import Sum
        from billing.models import Invoice, Payment
        from notifications.models import NotificationDelivery

        ctx = super().get_context_data(**kwargs)
        ctx["tenancies"] = self.object.tenancies.select_related("house", "house__estate")

        # Summary stats
        paid_agg = (
            Payment.objects.filter(
                tenant=self.object,
                approval_status__in=["APPROVED", "AUTO_APPROVED"],
            ).aggregate(total=Sum("amount"), n=Sum("amount"))
        )
        tenant_invoices = Invoice.objects.filter(tenant_house__tenant=self.object)
        ctx["summary"] = {
            "active_tenancies": self.object.tenancies.filter(status="ACTIVE").count(),
            "total_paid": paid_agg["total"] or 0,
            "invoices_total": tenant_invoices.count(),
            "open_invoices": tenant_invoices.exclude(status__in=["PAID", "VOIDED", "CANCELLED"]).count(),
        }

        # ---- Payments: search + pagination ----
        req = self.request
        allowed_sizes = [50, 100, 150, 250]
        q_pay = (req.GET.get("pq") or "").strip()
        pay_qs = (
            Payment.objects.filter(tenant=self.object)
            .select_related("bank_account")
            .prefetch_related("allocations__invoice__tenant_house__house")
            .order_by("-received_at")
        )
        if q_pay:
            from django.db.models import Q
            qobj = Q(number__icontains=q_pay) | Q(reference_number__icontains=q_pay)
            # If the term is numeric, also match on amount.
            try:
                from decimal import Decimal as _D
                qobj |= Q(amount=_D(q_pay.replace(",", "")))
            except Exception:
                pass
            pay_qs = pay_qs.filter(qobj)

        try:
            pay_size = int(req.GET.get("pps") or 50)
        except (TypeError, ValueError):
            pay_size = 50
        if pay_size not in allowed_sizes:
            pay_size = 50
        pay_paginator = Paginator(pay_qs, pay_size)
        try:
            pay_page_num = int(req.GET.get("pp") or 1)
        except (TypeError, ValueError):
            pay_page_num = 1
        pay_page = pay_paginator.get_page(pay_page_num)

        ctx.update({
            "payments_page": pay_page,
            "payments": pay_page.object_list,
            "payments_total": pay_paginator.count,
            "payments_q": q_pay,
            "payments_size": pay_size,
            "payments_sizes": allowed_sizes,
        })

        # ---- Messages: pagination only (same size options) ----
        notif_qs = (
            NotificationDelivery.objects.filter(tenant=self.object)
            .order_by("-created_at")
        )
        try:
            notif_size = int(req.GET.get("mps") or 50)
        except (TypeError, ValueError):
            notif_size = 50
        if notif_size not in allowed_sizes:
            notif_size = 50
        notif_paginator = Paginator(notif_qs, notif_size)
        try:
            notif_page_num = int(req.GET.get("mp") or 1)
        except (TypeError, ValueError):
            notif_page_num = 1
        notif_page = notif_paginator.get_page(notif_page_num)
        ctx.update({
            "notifications_page": notif_page,
            "notifications": notif_page.object_list,
            "notifications_total": notif_paginator.count,
            "notifications_size": notif_size,
            "notifications_sizes": allowed_sizes,
        })

        ctx["active_tab"] = req.GET.get("tab") or "overview"
        return ctx


class TenantCreateView(RoleRequiredMixin, CreateView):
    required_roles = STAFF_ROLES
    model = Tenant
    form_class = TenantForm
    template_name = "core/tenant_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Tenant created.")
        return super().form_valid(form)


class TenantUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = STAFF_ROLES
    model = Tenant
    form_class = TenantForm
    template_name = "core/tenant_form.html"

    def dispatch(self, request, *args, **kwargs):
        # Server-side profile edit restriction: a tenant cannot edit their own profile.
        tenant = self.get_object()
        if tenant.user_id and tenant.user_id == request.user.id and not has_any_role(
            request.user, *STAFF_ROLES
        ):
            raise PermissionDenied("Tenants cannot edit their own profile.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Tenant updated.")
        return super().form_valid(form)


class TenantDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES

    def post(self, request, pk):
        obj = get_object_or_404(Tenant, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "Tenant deleted.")
        return redirect("core:tenant-list")


class TenantMessageView(RoleRequiredMixin, View):
    """Send an ad-hoc Email / SMS / WhatsApp to a tenant. Every send is
    written to `AuditLog` and creates a `NotificationDelivery` row so the
    message appears in the delivery log as well."""
    required_roles = STAFF_ROLES

    def get(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        form = TenantMessageForm(
            tenant=tenant,
            initial={"channel": tenant.preferred_notification},
        )
        return render(request, "core/tenant_message.html",
                      {"tenant": tenant, "form": form})

    def post(self, request, pk):
        from accounts.models import AuditAction, AuditLog
        from notifications.models import Channel, Template
        from notifications.services import enqueue_notification

        tenant = get_object_or_404(Tenant, pk=pk)
        form = TenantMessageForm(request.POST, tenant=tenant)
        if not form.is_valid():
            return render(request, "core/tenant_message.html",
                          {"tenant": tenant, "form": form})

        channel = form.cleaned_data["channel"]
        subject = form.cleaned_data.get("subject", "").strip()
        message = form.cleaned_data["message"].strip()
        recipient = (
            tenant.email if channel == Channel.EMAIL else tenant.phone
        )
        context = {
            "tenant_name": tenant.full_name,
            "message": message,
            "subject": subject or "Message from Meili Property",
            "ad_hoc": True,
            "sent_by": request.user.get_full_name() or request.user.email,
        }
        delivery = enqueue_notification(
            template=Template.GENERIC,
            context=context,
            tenant=tenant,
            channel=channel,
            recipient=recipient,
            user=request.user,
        )
        # Override subject/body with the human-entered text so the delivery
        # log reflects what was actually sent.
        if subject:
            delivery.subject = subject
        delivery.body = message
        delivery.save(update_fields=["subject", "body"])

        AuditLog.record(
            AuditAction.NOTIFICATION_SENT,
            request=request,
            target=tenant,
            target_repr=f"{tenant.full_name} <{recipient}>",
            detail={
                "channel": channel,
                "subject": subject,
                "message": message,
                "delivery_id": delivery.pk,
            },
        )
        messages.success(
            request, f"{dict(_channel_label_map()).get(channel, channel)} queued to {recipient}."
        )
        return redirect("core:tenant-detail", pk=tenant.pk)


def _channel_label_map():
    from notifications.models import Channel
    return Channel.choices


# --- TenantHouse (lifecycle) ------------------------------------------------
class TenantHouseCreateView(RoleRequiredMixin, CreateView):
    """Attach a tenant to a house — starts as Prospect."""
    required_roles = STAFF_ROLES
    model = TenantHouse
    form_class = TenantHouseForm
    template_name = "core/tenanthouse_form.html"

    def get_initial(self):
        initial = super().get_initial()
        tenant_id = self.request.GET.get("tenant")
        if tenant_id:
            initial["tenant"] = tenant_id
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Tenancy created.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("core:tenant-detail", args=[self.object.tenant_id])


class TenantHouseActivateView(RoleRequiredMixin, View):
    """Prospect → Active. Marks house OCCUPIED."""
    required_roles = STAFF_ROLES

    def post(self, request, pk):
        th = get_object_or_404(TenantHouse, pk=pk)
        if th.status != TenantHouse.Status.PROSPECT:
            messages.error(request, "Only Prospect tenancies can be activated.")
            return redirect("core:tenant-detail", pk=th.tenant_id)
        # SPEC: a house can have only one ACTIVE tenancy at a time.
        clash = (
            TenantHouse.objects.filter(
                house=th.house, status=TenantHouse.Status.ACTIVE, is_deleted=False,
            ).exclude(pk=th.pk).first()
        )
        if clash:
            messages.error(
                request,
                f"Cannot activate — {th.house} already has an active tenant "
                f"({clash.tenant}). Exit that tenancy first."
            )
            return redirect("core:tenant-detail", pk=th.tenant_id)
        th.status = TenantHouse.Status.ACTIVE
        if not th.move_in_date:
            th.move_in_date = timezone.localdate()
        th.updated_by = request.user
        th.save()
        house = th.house
        house.occupancy_status = House.Occupancy.OCCUPIED
        house.updated_by = request.user
        house.save(update_fields=["occupancy_status", "updated_by", "updated_at"])
        messages.success(request, "Tenant activated — house marked Occupied.")
        return redirect("core:tenant-detail", pk=th.tenant_id)


class TenantHouseUpdateView(RoleRequiredMixin, UpdateView):
    """Edit a tenancy. Only PROSPECT rows can be moved between houses /
    have core fields edited — once ACTIVE or EXITED the row is part of the
    audit trail and stays locked except for the dedicated activate / exit
    workflows."""
    required_roles = STAFF_ROLES
    model = TenantHouse
    form_class = TenantHouseForm
    template_name = "core/tenanthouse_form.html"

    def dispatch(self, request, *args, **kwargs):
        th = self.get_object()
        if th.status != TenantHouse.Status.PROSPECT:
            messages.error(
                request,
                f"Tenancy is {th.get_status_display()} — only Prospect tenancies can be edited. "
                f"Use the activate/exit workflow instead."
            )
            return redirect("core:tenant-detail", pk=th.tenant_id)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Tenancy updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("core:tenant-detail", args=[self.object.tenant_id]) + "?tab=tenancies"


class TenantHouseExitView(RoleRequiredMixin, View):
    """Active → Exited. Marks house VACANT if no other active tenancies remain."""
    required_roles = STAFF_ROLES

    def post(self, request, pk):
        th = get_object_or_404(TenantHouse, pk=pk)
        if th.status != TenantHouse.Status.ACTIVE:
            messages.error(request, "Only Active tenancies can be exited.")
            return redirect("core:tenant-detail", pk=th.tenant_id)
        th.status = TenantHouse.Status.EXITED
        if not th.move_out_date:
            th.move_out_date = timezone.localdate()
        th.updated_by = request.user
        th.save()
        house = th.house
        still_active = house.tenancies.filter(status=TenantHouse.Status.ACTIVE).exists()
        if not still_active:
            house.occupancy_status = House.Occupancy.VACANT
            house.updated_by = request.user
            house.save(update_fields=["occupancy_status", "updated_by", "updated_at"])
        messages.success(request, "Tenancy exited.")
        return redirect("core:tenant-detail", pk=th.tenant_id)


# --- Employee ---------------------------------------------------------------
class EmployeeListView(RoleRequiredMixin, PaginatedListView):
    required_roles = ADMIN_ROLES
    model = Employee
    template_name = "core/employee_list.html"
    context_object_name = "employees"

    def get_queryset(self):
        qs = super().get_queryset().select_related("manager")
        q = (self.request.GET.get("q") or "").strip()
        active = self.request.GET.get("active") or ""
        trusted = self.request.GET.get("trusted") or ""
        employment_type = self.request.GET.get("employment_type") or ""
        if q:
            qs = qs.filter(
                Q(full_name__icontains=q) | Q(phone__icontains=q)
                | Q(job_title__icontains=q) | Q(id_number__icontains=q)
            )
        if active == "yes":
            qs = qs.filter(is_active=True)
        elif active == "no":
            qs = qs.filter(is_active=False)
        if trusted == "yes":
            qs = qs.filter(requires_checker=False)
        elif trusted == "no":
            qs = qs.filter(requires_checker=True)
        if employment_type:
            qs = qs.filter(employment_type=employment_type)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_q"] = self.request.GET.get("q", "")
        ctx["filter_active"] = self.request.GET.get("active", "")
        ctx["filter_trusted"] = self.request.GET.get("trusted", "")
        ctx["filter_employment_type"] = self.request.GET.get("employment_type", "")
        ctx["employment_type_choices"] = Employee.EmploymentType.choices
        return ctx


class EmployeeDetailView(RoleRequiredMixin, DetailView):
    required_roles = ADMIN_ROLES
    model = Employee
    template_name = "core/employee_detail.html"
    context_object_name = "employee"

    def get_context_data(self, **kwargs):
        from billing.models import ExpenseClaim

        ctx = super().get_context_data(**kwargs)
        emp = self.object
        transport = getattr(emp, "allowance_transport", 0) or 0
        housing = getattr(emp, "allowance_housing", 0) or 0
        airtime = getattr(emp, "allowance_airtime", 0) or 0
        other = getattr(emp, "allowance_other", 0) or 0
        base = getattr(emp, "base_salary", 0) or 0
        paye = getattr(emp, "paye_monthly", 0) or 0
        gross = base + transport + housing + airtime + other
        ctx["comp"] = {
            "base": base, "transport": transport, "housing": housing,
            "airtime": airtime, "other": other, "paye": paye,
            "gross": gross, "net": gross - paye,
        }
        ctx["expense_claims"] = (
            ExpenseClaim.objects.filter(claimant=emp)
            .order_by("-incurred_at", "-id")[:100]
        )
        ctx["active_tab"] = self.request.GET.get("tab") or "overview"
        return ctx


class EmployeeCreateView(RoleRequiredMixin, CreateView):
    required_roles = ADMIN_ROLES
    model = Employee
    form_class = EmployeeForm
    template_name = "core/employee_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Employee created.")
        return super().form_valid(form)


class EmployeeUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ADMIN_ROLES
    model = Employee
    form_class = EmployeeForm
    template_name = "core/employee_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Employee updated.")
        return super().form_valid(form)


class EmployeeDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES

    def post(self, request, pk):
        obj = get_object_or_404(Employee, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "Employee deleted.")
        return redirect("core:employee-list")


# --- Supplier ---------------------------------------------------------------
class SupplierListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = Supplier
    template_name = "core/supplier_list.html"
    context_object_name = "suppliers"

    def get_queryset(self):
        qs = super().get_queryset()
        q = (self.request.GET.get("q") or "").strip()
        kind = self.request.GET.get("kind") or ""
        active = self.request.GET.get("active") or ""
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(contact_person__icontains=q)
                | Q(phone__icontains=q) | Q(email__icontains=q)
                | Q(tax_id__icontains=q)
            )
        if kind:
            qs = qs.filter(kind=kind)
        if active == "yes":
            qs = qs.filter(is_active=True)
        elif active == "no":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_q"] = self.request.GET.get("q", "")
        ctx["filter_kind"] = self.request.GET.get("kind", "")
        ctx["filter_active"] = self.request.GET.get("active", "")
        ctx["kind_choices"] = Supplier.Kind.choices
        return ctx


class SupplierDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = Supplier
    template_name = "core/supplier_detail.html"
    context_object_name = "supplier"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        from django.db.models import Q, Sum
        from billing.models import SupplierPayment

        ctx = super().get_context_data(**kwargs)
        req = self.request
        allowed_sizes = [50, 100, 150, 250]

        q_pay = (req.GET.get("pq") or "").strip()
        pay_qs = (
            SupplierPayment.objects.filter(supplier=self.object)
            .select_related("bank_account", "related_house", "related_house__estate")
            .order_by("-paid_at", "-id")
        )
        if q_pay:
            qobj = (
                Q(number__icontains=q_pay)
                | Q(reference_number__icontains=q_pay)
                | Q(invoice_reference__icontains=q_pay)
                | Q(service_description__icontains=q_pay)
            )
            try:
                from decimal import Decimal as _D
                qobj |= Q(amount=_D(q_pay.replace(",", "")))
            except Exception:
                pass
            pay_qs = pay_qs.filter(qobj)
        try:
            pay_size = int(req.GET.get("pps") or 50)
        except (TypeError, ValueError):
            pay_size = 50
        if pay_size not in allowed_sizes:
            pay_size = 50
        pay_paginator = Paginator(pay_qs, pay_size)
        try:
            pay_page_num = int(req.GET.get("pp") or 1)
        except (TypeError, ValueError):
            pay_page_num = 1
        pay_page = pay_paginator.get_page(pay_page_num)

        total_paid = (
            SupplierPayment.objects.filter(
                supplier=self.object,
                approval_status__in=["APPROVED", "AUTO_APPROVED"],
            ).aggregate(s=Sum("amount"))["s"] or 0
        )

        ctx.update({
            "payments_page": pay_page,
            "payments": pay_page.object_list,
            "payments_total": pay_paginator.count,
            "payments_q": q_pay,
            "payments_size": pay_size,
            "payments_sizes": allowed_sizes,
            "total_paid": total_paid,
            "active_tab": req.GET.get("tab") or "overview",
        })
        return ctx


class SupplierCreateView(RoleRequiredMixin, CreateView):
    required_roles = ADMIN_ROLES + ("FINANCE", "ACCOUNT_MANAGER")
    model = Supplier
    form_class = SupplierForm
    template_name = "core/supplier_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Supplier created.")
        return super().form_valid(form)


class SupplierUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ADMIN_ROLES + ("FINANCE", "ACCOUNT_MANAGER")
    model = Supplier
    form_class = SupplierForm
    template_name = "core/supplier_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Supplier updated.")
        return super().form_valid(form)


class SupplierDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES

    def post(self, request, pk):
        obj = get_object_or_404(Supplier, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "Supplier deleted.")
        return redirect("core:supplier-list")


# ---------------------------------------------------------------------------
# Prospects report — all PROSPECT tenancies across houses, regardless of
# current occupancy. Lets sales/accounts see who's queued for each house.
# ---------------------------------------------------------------------------
class ProspectsReportView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    template_name = "core/prospects_report.html"
    context_object_name = "prospects"

    def get_queryset(self):
        qs = (
            TenantHouse.objects.filter(status=TenantHouse.Status.PROSPECT)
            .select_related("tenant", "house", "house__estate", "sales_rep")
            .order_by("house__estate__name", "house__house_number", "-created_at")
        )
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(tenant__full_name__icontains=q)
                | Q(house__house_number__icontains=q)
                | Q(house__name__icontains=q)
                | Q(house__estate__name__icontains=q)
            )
        occupancy = self.request.GET.get("occupancy")
        if occupancy:
            qs = qs.filter(house__occupancy_status=occupancy)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            columns = [
                ("Estate", "house.estate.name"),
                ("House", lambda t: str(t.house)),
                ("House occupancy", lambda t: t.house.get_occupancy_status_display()),
                ("Prospect tenant", "tenant.full_name"),
                ("Tenant phone", "tenant.phone"),
                ("Tenant email", "tenant.email"),
                ("Sales rep", lambda t: t.sales_rep.full_name if t.sales_rep_id else ""),
                ("Created", "created_at"),
                ("Note", "invoice_generation_note"),
            ]
            return export_csv(self.get_queryset(), columns, "prospects_report")
        return super().get(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Collections targets + bonus brackets (Phase F.2)
# ---------------------------------------------------------------------------
from datetime import date as _date
from decimal import Decimal as _Decimal

from .forms import CollectionsBonusBracketForm, CollectionsTargetForm
from .models import CollectionsBonusBracket, CollectionsTarget


class CollectionsTargetListView(RoleRequiredMixin, PaginatedListView):
    required_roles = ADMIN_ROLES + ("FINANCE",)
    model = CollectionsTarget
    template_name = "core/collections_target_list.html"
    context_object_name = "targets"

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee").order_by("-month", "employee__full_name")
        emp = self.request.GET.get("employee")
        if emp:
            qs = qs.filter(employee_id=emp)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["employees"] = Employee.objects.filter(is_active=True).order_by("full_name")
        return ctx


class CollectionsTargetCreateView(RoleRequiredMixin, CreateView):
    required_roles = ADMIN_ROLES + ("FINANCE",)
    model = CollectionsTarget
    form_class = CollectionsTargetForm
    template_name = "core/collections_target_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Target saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("core:collections-target-list")


class CollectionsTargetUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ADMIN_ROLES + ("FINANCE",)
    model = CollectionsTarget
    form_class = CollectionsTargetForm
    template_name = "core/collections_target_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Target updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("core:collections-target-list")


class CollectionsTargetDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES + ("FINANCE",)

    def post(self, request, pk):
        obj = get_object_or_404(CollectionsTarget, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "Target removed.")
        return redirect("core:collections-target-list")


class CollectionsBracketListView(RoleRequiredMixin, PaginatedListView):
    required_roles = ADMIN_ROLES + ("FINANCE",)
    model = CollectionsBonusBracket
    template_name = "core/collections_bracket_list.html"
    context_object_name = "brackets"


class CollectionsBracketCreateView(RoleRequiredMixin, CreateView):
    required_roles = ADMIN_ROLES + ("FINANCE",)
    model = CollectionsBonusBracket
    form_class = CollectionsBonusBracketForm
    template_name = "core/collections_bracket_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Bracket saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("core:collections-bracket-list")


class CollectionsBracketUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ADMIN_ROLES + ("FINANCE",)
    model = CollectionsBonusBracket
    form_class = CollectionsBonusBracketForm
    template_name = "core/collections_bracket_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Bracket updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("core:collections-bracket-list")


class CollectionsBracketDeleteView(RoleRequiredMixin, View):
    required_roles = ADMIN_ROLES + ("FINANCE",)

    def post(self, request, pk):
        obj = get_object_or_404(CollectionsBonusBracket, pk=pk)
        obj.soft_delete(user=request.user)
        messages.success(request, "Bracket removed.")
        return redirect("core:collections-bracket-list")


class CollectionsPerformanceReportView(RoleRequiredMixin, View):
    """Per-employee monthly collections vs target with bonus computation."""
    required_roles = STAFF_ROLES

    def get(self, request):
        from . import collections as svc
        # ---- filters ----
        today = _date.today()
        month_str = request.GET.get("month") or today.replace(day=1).isoformat()
        try:
            month = _date.fromisoformat(month_str).replace(day=1)
        except ValueError:
            month = today.replace(day=1)
        emp_id = request.GET.get("employee") or ""
        house_id = request.GET.get("house") or ""
        estate_id = request.GET.get("estate") or ""
        employees = None
        if emp_id:
            employees = list(Employee.objects.filter(pk=emp_id))
        house = House.objects.filter(pk=house_id).first() if house_id else None
        estate = Estate.objects.filter(pk=estate_id).first() if estate_id else None

        rows = svc.build_performance_rows(
            month=month, employees=employees, house=house, estate=estate,
        )

        # Aggregates
        total_target = sum((r.target for r in rows), _Decimal("0"))
        total_collected = sum((r.collected for r in rows), _Decimal("0"))
        total_bonus = sum((r.bonus for r in rows), _Decimal("0"))

        # CSV export
        if request.GET.get("export") == "csv":
            from .utils import export_csv
            cols = [
                ("Employee", lambda r: r.employee.full_name),
                ("Month", lambda r: r.month.strftime("%Y-%m")),
                ("Target (UGX)", "target"),
                ("Collected (UGX)", "collected"),
                ("Attainment %", lambda r: f"{r.attainment_pct:.1f}" if r.attainment_pct is not None else ""),
                ("Bonus bracket", lambda r: r.bracket.label if r.bracket else ""),
                ("Bonus rate %", lambda r: r.bracket.rate_percent if r.bracket else ""),
                ("Bonus (UGX)", "bonus"),
            ]
            return export_csv(rows, cols, f"collections_performance_{month.strftime('%Y%m')}")

        return render(request, "core/collections_performance.html", {
            "rows": rows,
            "month": month,
            "filter_employee": emp_id,
            "filter_house": house_id,
            "filter_estate": estate_id,
            "employees": Employee.objects.filter(is_active=True).order_by("full_name"),
            "houses": House.objects.select_related("estate").order_by("estate__name", "house_number"),
            "estates": Estate.objects.order_by("name"),
            "total_target": total_target,
            "total_collected": total_collected,
            "total_bonus": total_bonus,
            "active_brackets": CollectionsBonusBracket.objects.filter(is_active=True).order_by("min_amount"),
        })
