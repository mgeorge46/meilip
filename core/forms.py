"""Forms for core entities."""
from django import forms

from .models import (
    Employee,
    Estate,
    House,
    Landlord,
    Supplier,
    Tenant,
    TenantHouse,
)


class LandlordForm(forms.ModelForm):
    class Meta:
        model = Landlord
        fields = [
            "full_name", "phone", "email", "id_number", "is_meili_owned",
            "bank_name", "bank_account_name", "bank_account_number", "bank_branch",
            "status", "notes",
        ]


_SETTINGS_FIELDS = [
    "currency", "billing_cycle", "billing_mode", "prorata_mode",
    "commission_type", "commission_scope", "commission_amount", "commission_percent",
    "tax_type", "security_deposit_policy", "initial_deposit_policy",
    "account_manager", "collections_person",
    "water_billed_separately", "garbage_billed_separately",
]


class EstateForm(forms.ModelForm):
    class Meta:
        model = Estate
        fields = ["landlord", "name", "location", "description"] + _SETTINGS_FIELDS


class HouseForm(forms.ModelForm):
    class Meta:
        model = House
        fields = [
            "estate", "landlord", "house_number", "name", "description",
            "periodic_rent", "occupancy_status",
        ] + _SETTINGS_FIELDS


class TenantForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = [
            "full_name", "phone", "email", "id_number",
            "next_of_kin_name", "next_of_kin_phone",
            "preferred_notification", "preferred_receipt", "sales_rep",
        ]


class TenantHouseForm(forms.ModelForm):
    class Meta:
        model = TenantHouse
        fields = [
            "tenant", "house", "status",
            "move_in_date", "move_out_date", "billing_start_date",
            "security_deposit", "initial_deposit",
            "sales_rep", "account_manager", "collections_person",
        ]


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["user", "full_name", "phone", "id_number", "manager", "requires_checker", "is_active"]


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name", "contact_person", "phone", "email", "kind", "tax_id",
            "bank_name", "bank_account_number", "is_active",
        ]
