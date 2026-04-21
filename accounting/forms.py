from django import forms
from django.forms import inlineformset_factory

from .models import Account, BankAccount, JournalEntry, JournalEntryLine


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


class JournalEntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["entry_date", "memo", "source"]
        widgets = {
            "entry_date": forms.DateInput(attrs={"type": "date"}),
        }


class JournalEntryLineForm(forms.ModelForm):
    class Meta:
        model = JournalEntryLine
        fields = ["account", "debit", "credit", "description"]


JournalEntryLineFormSet = inlineformset_factory(
    JournalEntry,
    JournalEntryLine,
    form=JournalEntryLineForm,
    extra=2,
    min_num=2,
    validate_min=True,
    can_delete=True,
)
