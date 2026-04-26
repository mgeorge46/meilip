"""Billing forms — explicit, minimal fields; Select2-compatible."""
from django import forms

from core.models import TenantHouse

from .models import (
    AdHocCharge,
    CreditNote,
    ExpenseClaim,
    Invoice,
    InvoiceVoid,
    LandlordPayout,
    Payment,
    Refund,
    SupplierPayment,
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


class LandlordPayoutForm(forms.ModelForm):
    class Meta:
        model = LandlordPayout
        fields = [
            "landlord", "amount", "method", "bank_account",
            "period_from", "period_to", "reference_number", "paid_at", "notes",
        ]
        widgets = {
            "landlord": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(attrs={"class": "form-control text-end num", "inputmode": "numeric"}),
            "method": forms.Select(attrs={"class": "form-select"}),
            "bank_account": forms.Select(attrs={"class": "form-select"}),
            "period_from": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "period_to": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "reference_number": forms.TextInput(attrs={"class": "form-control"}),
            "paid_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class SupplierPaymentForm(forms.ModelForm):
    class Meta:
        model = SupplierPayment
        fields = [
            "supplier", "amount", "method", "bank_account",
            "service_description", "invoice_reference", "reference_number",
            "related_house", "paid_at", "notes",
        ]
        widgets = {
            "supplier": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(attrs={"class": "form-control text-end num", "inputmode": "numeric"}),
            "method": forms.Select(attrs={"class": "form-select"}),
            "bank_account": forms.Select(attrs={"class": "form-select"}),
            "service_description": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Plumbing repair — Buziga A2"}),
            "invoice_reference": forms.TextInput(attrs={"class": "form-control"}),
            "reference_number": forms.TextInput(attrs={"class": "form-control"}),
            "related_house": forms.Select(attrs={"class": "form-select"}),
            "paid_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class ExpenseClaimForm(forms.ModelForm):
    """Employee-facing form. `reimbursement_bank` is NOT here — Finance picks
    that when approving the claim."""
    class Meta:
        model = ExpenseClaim
        fields = [
            "claimant", "category", "description", "related_house",
            "amount", "incurred_at", "receipt_photo", "notes",
        ]
        widgets = {
            "claimant": forms.Select(attrs={"class": "form-select"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "related_house": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(attrs={"class": "form-control text-end num", "inputmode": "numeric"}),
            "incurred_at": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "receipt_photo": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*,application/pdf"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class ExpenseClaimApproveForm(forms.Form):
    """Finance-only form used in the Approvals queue: pick the reimbursement
    bank account, then approve. The chosen bank is written onto the claim
    BEFORE approve() is called, so the GL posting signal has everything it
    needs to post Dr <expense> / Cr <bank.ledger>."""
    reimbursement_bank = forms.ModelChoiceField(
        queryset=None, required=True,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
        label="Reimburse from",
    )

    def __init__(self, *args, **kwargs):
        from accounting.models import BankAccount
        super().__init__(*args, **kwargs)
        self.fields["reimbursement_bank"].queryset = (
            BankAccount.objects.filter(is_active=True).order_by("name")
        )
