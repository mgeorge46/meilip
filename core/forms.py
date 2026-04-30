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
    """Attach a tenant to a house.

    On CREATE: only PROSPECT or ACTIVE are selectable — EXITED is reserved for
    the tenancy-exit workflow and never chosen manually at attach time.
    On EDIT: all three statuses are shown because admins sometimes need to
    correct records.
    """
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # On a NEW tenancy: force status to PROSPECT (renamed in the UI to
        # "Preparing to move in"). Activation is a separate, deliberate
        # action (the green Activate button) so users can't accidentally
        # spawn ACTIVE rows that bypass the activation flow — which would
        # leave the house mis-flagged Vacant and skip the first-invoice
        # auto-generation.
        if self.instance.pk is None:
            self.fields["status"].choices = [
                (TenantHouse.Status.PROSPECT, "Preparing to move in (Prospect)"),
            ]
            self.initial["status"] = TenantHouse.Status.PROSPECT
            self.fields["status"].help_text = (
                "New tenancies start as Prospect. Use the Activate button on "
                "the tenant's profile to flip to Active — that step also "
                "marks the house Occupied and auto-generates the first invoice."
            )


class ActiveTenancyEditForm(forms.ModelForm):
    """Limited edits allowed on an ACTIVE tenancy.

    Excludes anything that would disturb a running billing cycle: tenant,
    house, status, billing_start_date, move_in_date are NOT editable. Only
    soft fields (planned exit date, owners, deposit references, notes)
    can be touched.
    """
    class Meta:
        model = TenantHouse
        fields = [
            "move_out_date",
            "security_deposit", "initial_deposit",
            "sales_rep", "account_manager", "collections_person",
        ]
        widgets = {
            "move_out_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "security_deposit": forms.NumberInput(attrs={"class": "form-control text-end num"}),
            "initial_deposit": forms.NumberInput(attrs={"class": "form-control text-end num"}),
            "sales_rep": forms.Select(attrs={"class": "form-select"}),
            "account_manager": forms.Select(attrs={"class": "form-select"}),
            "collections_person": forms.Select(attrs={"class": "form-select"}),
        }


class TenancyPauseResumeForm(forms.Form):
    """Pause / resume / stop recurring invoice generation on a tenancy.

    The maker MUST supply a reason; the audit trail is captured in the
    `invoice_generation_note` field on TenantHouse.
    """
    STATUS_CHOICES = [
        (TenantHouse.InvoiceGenerationStatus.ACTIVE, "Resume (Active)"),
        (TenantHouse.InvoiceGenerationStatus.PAUSED, "Pause"),
        (TenantHouse.InvoiceGenerationStatus.STOPPED, "Stop completely"),
    ]
    new_status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    reason = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. Tenant on extended leave 2026-05 to 2026-07",
        }),
    )
    effective_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        help_text="Optional — informational only. Stored in the audit note. Cannot be in the past.",
    )

    def clean_effective_date(self):
        d = self.cleaned_data.get("effective_date")
        if d:
            from django.utils import timezone as _tz
            today = _tz.localdate()
            if d < today:
                raise forms.ValidationError(
                    "Effective date cannot be in the past. Use today or a future date."
                )
        return d


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


# ---------------------------------------------------------------------------
# Collections targets + bonus brackets (Phase F.2)
# ---------------------------------------------------------------------------
from .models import CollectionsBonusBracket, CollectionsTarget


class CollectionsTargetForm(forms.ModelForm):
    class Meta:
        model = CollectionsTarget
        fields = ["employee", "month", "target_amount", "notes"]
        widgets = {
            "employee": forms.Select(attrs={"class": "form-select"}),
            "month": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "target_amount": forms.NumberInput(attrs={"class": "form-control text-end num", "inputmode": "numeric"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class CollectionsBonusBracketForm(forms.ModelForm):
    class Meta:
        model = CollectionsBonusBracket
        fields = ["label", "min_amount", "max_amount", "rate_percent", "is_active"]
        widgets = {
            "label": forms.TextInput(attrs={"class": "form-control"}),
            "min_amount": forms.NumberInput(attrs={"class": "form-control text-end num", "inputmode": "numeric"}),
            "max_amount": forms.NumberInput(attrs={"class": "form-control text-end num", "inputmode": "numeric"}),
            "rate_percent": forms.NumberInput(attrs={"class": "form-control text-end num", "step": "0.01"}),
        }
