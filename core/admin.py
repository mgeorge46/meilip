from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    BillingCycle,
    Currency,
    Employee,
    Estate,
    House,
    Landlord,
    Supplier,
    TaxType,
    Tenant,
    TenantHouse,
)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "decimal_places", "is_primary", "is_active")
    list_filter = ("is_active", "is_primary")
    search_fields = ("code", "name")


@admin.register(BillingCycle)
class BillingCycleAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "count", "is_active")
    list_filter = ("unit", "is_active")
    search_fields = ("name",)


@admin.register(TaxType)
class TaxTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "kind", "rate", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("code", "name")


@admin.register(Landlord)
class LandlordAdmin(SimpleHistoryAdmin):
    list_display = ("full_name", "phone", "email", "is_meili_owned", "status", "is_deleted")
    list_filter = ("status", "is_meili_owned", "is_deleted")
    search_fields = ("full_name", "phone", "email")
    autocomplete_fields = ("user",)


@admin.register(Estate)
class EstateAdmin(SimpleHistoryAdmin):
    list_display = ("name", "landlord", "location", "is_deleted")
    list_filter = ("is_deleted", "landlord")
    search_fields = ("name", "location", "landlord__full_name")
    autocomplete_fields = ("landlord", "currency", "billing_cycle", "tax_type", "account_manager", "collections_person")


@admin.register(House)
class HouseAdmin(SimpleHistoryAdmin):
    list_display = ("house_number", "name", "estate", "occupancy_status", "periodic_rent", "is_deleted")
    list_filter = ("occupancy_status", "is_deleted", "estate")
    search_fields = ("house_number", "name", "estate__name")
    autocomplete_fields = ("estate", "landlord", "currency", "billing_cycle", "tax_type", "account_manager", "collections_person")


@admin.register(Employee)
class EmployeeAdmin(SimpleHistoryAdmin):
    list_display = ("full_name", "user", "manager", "requires_checker", "is_active", "is_deleted")
    list_filter = ("is_active", "requires_checker", "is_deleted")
    search_fields = ("full_name", "user__email")
    autocomplete_fields = ("user", "manager")


@admin.register(Tenant)
class TenantAdmin(SimpleHistoryAdmin):
    list_display = ("full_name", "phone", "email", "preferred_notification", "is_deleted")
    list_filter = ("preferred_notification", "preferred_receipt", "is_deleted")
    search_fields = ("full_name", "phone", "email")
    autocomplete_fields = ("user", "sales_rep")


@admin.register(TenantHouse)
class TenantHouseAdmin(SimpleHistoryAdmin):
    list_display = ("tenant", "house", "status", "move_in_date", "move_out_date", "is_deleted")
    list_filter = ("status", "is_deleted")
    search_fields = ("tenant__full_name", "house__house_number", "house__estate__name")
    autocomplete_fields = ("tenant", "house", "sales_rep", "account_manager", "collections_person")


@admin.register(Supplier)
class SupplierAdmin(SimpleHistoryAdmin):
    list_display = ("name", "kind", "phone", "email", "is_active", "is_deleted")
    list_filter = ("kind", "is_active", "is_deleted")
    search_fields = ("name", "phone", "email")
