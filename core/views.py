"""Core CRUD views: Landlord, Estate, House, Tenant, TenantHouse, Employee, Supplier.

All views require authentication. Role restrictions per SPEC §16.9 (permission
matrix) are applied via RoleRequiredMixin.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
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
        return super().get_queryset().select_related("landlord")


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
        return super().get_queryset().select_related("estate", "landlord")


SETTINGS_DISPLAY_FIELDS = [
    "currency", "billing_cycle", "billing_mode", "prorata_mode",
    "commission_type", "commission_scope", "commission_amount", "commission_percent",
    "tax_type", "security_deposit_policy", "initial_deposit_policy",
    "account_manager", "collections_person",
    "water_billed_separately", "garbage_billed_separately",
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
