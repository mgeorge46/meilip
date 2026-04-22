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
        ctx = super().get_context_data(**kwargs)
        ctx["tenancies"] = self.object.tenancies.select_related("house", "house__estate")
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
