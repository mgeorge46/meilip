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


# Local lookup that mirrors notifications.models.Channel — duplicated here
# to avoid a hard import cycle during app loading.
_MESSAGE_CHANNEL_CHOICES = (
    ("SMS", "SMS"),
    ("WHATSAPP", "WhatsApp"),
    ("EMAIL", "Email"),
)


class TenantMessageForm(forms.Form):
    """Ad-hoc direct message to a tenant — Email / SMS / WhatsApp."""
    channel = forms.ChoiceField(choices=_MESSAGE_CHANNEL_CHOICES)
    subject = forms.CharField(
        required=False, max_length=120,
        help_text="Used as the email subject. Ignored for SMS / WhatsApp.",
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5, "maxlength": 1500}),
        min_length=2, max_length=1500,
    )

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant

    def clean(self):
        data = super().clean()
        channel = data.get("channel")
        if not self.tenant:
            raise forms.ValidationError("No tenant specified.")
        if channel == "EMAIL" and not self.tenant.email:
            raise forms.ValidationError("Tenant has no email address on file.")
        if channel in ("SMS", "WHATSAPP") and not self.tenant.phone:
            raise forms.ValidationError("Tenant has no phone number on file.")
        return data


class LandlordForm(forms.ModelForm):
    class Meta:
        model = Landlord
        fields = [
            "first_name", "last_name", "other_names",
            "phone", "whatsapp_number", "email", "id_number", "is_meili_owned",
            "bank_name", "bank_account_name", "bank_account_number", "bank_branch",
            "status", "preferred_statement_channel", "notes",
        ]


_SETTINGS_FIELDS = [
    "currency", "billing_cycle", "billing_mode", "prorata_mode",
    "commission_type", "commission_scope", "commission_amount", "commission_percent",
    "tax_type", "security_deposit_policy", "initial_deposit_policy",
    "account_manager", "collections_person",
    "water_billed_separately", "garbage_billed_separately",
    "security_billed_separately", "electricity_billed_separately",
    "other_bills_billed_separately", "other_bills_description",
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
            "first_name", "last_name", "other_names",
            "phone", "email", "id_number",
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
        widgets = {
            "move_in_date": forms.DateInput(attrs={"type": "date"}),
            "move_out_date": forms.DateInput(attrs={"type": "date"}),
            "billing_start_date": forms.DateInput(attrs={"type": "date"}),
        }


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            # Identity
            "user",
            "first_name", "last_name", "other_names",
            "phone", "id_number", "manager",
            "job_title", "employment_type", "hire_date",
            # Status / approval
            "requires_checker", "is_active",
            # Payroll earnings
            "base_salary",
            "allowance_transport", "allowance_housing",
            "allowance_airtime", "allowance_other",
            # Statutory / deductions (NSSF removed per simplified payroll)
            "paye_monthly", "other_deduction",
            # Bank / tax references
            "bank_name", "bank_account_name", "bank_account_number", "bank_branch",
            "tin",
        ]
        widgets = {
            "hire_date": forms.DateInput(attrs={"type": "date"}),
        }


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name", "contact_person", "phone", "email", "kind", "tax_id",
            "bank_name", "bank_account_number", "is_active",
        ]
