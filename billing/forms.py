"""Billing forms — explicit, minimal fields; Select2-compatible."""
from django import forms

from core.models import TenantHouse

from .models import (
    AdHocCharge,
    CreditNote,
    Invoice,
    InvoiceVoid,
    Payment,
    Refund,
)


class ManualInvoiceForm(forms.ModelForm):
    """For Admin/Finance/Account Manager manual invoice creation — backdated
    invoices require a reason (enforced server-side)."""

    class Meta:
        model = Invoice
        fields = [
            "tenant_house", "period_from", "period_to", "issue_date",
            "due_date", "rent_amount", "is_backdated", "backdate_reason", "notes",
        ]
        widgets = {
            "period_from": forms.DateInput(attrs={"type": "date"}),
            "period_to": forms.DateInput(attrs={"type": "date"}),
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "backdate_reason": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        data = super().clean()
        if data.get("is_backdated") and not data.get("backdate_reason", "").strip():
            raise forms.ValidationError(
                "Backdated invoices require a written reason."
            )
        return data


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["tenant", "amount", "method", "bank_account", "reference_number", "received_at"]
        widgets = {"received_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class AdHocChargeForm(forms.ModelForm):
    class Meta:
        model = AdHocCharge
        fields = ["tenant_house", "description", "amount", "target", "bill_on_or_after"]
        widgets = {"bill_on_or_after": forms.DateInput(attrs={"type": "date"})}


class InvoiceVoidForm(forms.ModelForm):
    class Meta:
        model = InvoiceVoid
        fields = ["invoice", "reason_category", "reason", "void_date"]
        widgets = {
            "void_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }


class CreditNoteForm(forms.ModelForm):
    class Meta:
        model = CreditNote
        fields = ["original_invoice", "amount", "reason_category", "reason"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 3})}


class RefundForm(forms.ModelForm):
    class Meta:
        model = Refund
        fields = [
            "tenant", "tenant_house", "amount", "method", "source",
            "source_account", "bank_account", "destination_details",
            "reference_number", "linked_credit_note", "reason",
        ]
        widgets = {"reason": forms.Textarea(attrs={"rows": 3})}


class RejectionForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=True,
        help_text="Provide a reason visible to the maker.",
    )


class InvoicePauseForm(forms.Form):
    status = forms.ChoiceField(
        choices=TenantHouse.InvoiceGenerationStatus.choices
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
