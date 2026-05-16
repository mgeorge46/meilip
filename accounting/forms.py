from django import forms
from django.forms import inlineformset_factory

from .models import Account, BankAccount, InternalTransfer, JournalEntry, JournalEntryLine


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = [
            "code", "name", "account_type", "parent", "system_code",
            "description", "currency", "is_postable", "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only non-postable accounts are eligible parents
        self.fields["parent"].queryset = Account.objects.filter(is_postable=False, is_active=True).order_by("code")
        self.fields["parent"].required = False
        self.fields["system_code"].required = False
        self.fields["description"].required = False
        self.fields["currency"].required = False


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = [
            "name", "kind", "bank_name", "account_number", "branch",
            "mobile_provider", "mobile_number", "currency", "ledger_account", "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only POSTABLE leaf accounts can be tied to a BankAccount — picking
        # a rollup like "1200 Bank Accounts" causes JE posting to fail with
        # "Cannot post to non-postable account 1200".
        self.fields["ledger_account"].queryset = (
            Account.objects
            .filter(is_postable=True, is_active=True, account_type__category="ASSET")
            .order_by("code")
        )
        self.fields["ledger_account"].help_text = (
            "Leaf asset account this bank/cash channel posts to. "
            "Rollup parents are filtered out automatically."
        )


class JournalEntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["entry_date", "memo", "source"]
        widgets = {
            "entry_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "memo": forms.TextInput(attrs={"class": "form-control", "placeholder": "Short description of this entry"}),
            "source": forms.Select(attrs={"class": "form-select"}),
        }


class JournalEntryLineForm(forms.ModelForm):
    class Meta:
        model = JournalEntryLine
        fields = ["account", "debit", "credit", "description", "receipt_image"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].widget.attrs.update({"class": "form-select"})
        self.fields["debit"].widget.attrs.update({"class": "form-control text-end num", "placeholder": "0", "inputmode": "numeric"})
        self.fields["credit"].widget.attrs.update({"class": "form-control text-end num", "placeholder": "0", "inputmode": "numeric"})
        self.fields["description"].widget.attrs.update({"class": "form-control", "placeholder": "Optional line note"})
        self.fields["receipt_image"].widget.attrs.update({"class": "form-control", "accept": "image/*"})


JournalEntryLineFormSet = inlineformset_factory(
    JournalEntry,
    JournalEntryLine,
    form=JournalEntryLineForm,
    extra=2,
    min_num=2,
    validate_min=True,
    can_delete=True,
)


class InternalTransferForm(forms.ModelForm):
    class Meta:
        model = InternalTransfer
        fields = ["source_bank", "destination_bank", "amount", "transfer_date", "reference", "memo"]
        widgets = {
            "transfer_date": forms.DateInput(attrs={"type": "date"}),
            "memo": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active = BankAccount.objects.filter(is_active=True).order_by("name")
        self.fields["source_bank"].queryset = active
        self.fields["destination_bank"].queryset = active
