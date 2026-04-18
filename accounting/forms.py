from django import forms
from django.forms import inlineformset_factory

from .models import BankAccount, JournalEntry, JournalEntryLine


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
