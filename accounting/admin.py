from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Account, AccountType, BankAccount, JournalEntry, JournalEntryLine


@admin.register(AccountType)
class AccountTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "normal_balance", "ordering")
    list_filter = ("category", "normal_balance")
    search_fields = ("code", "name")


@admin.register(Account)
class AccountAdmin(SimpleHistoryAdmin):
    list_display = ("code", "name", "account_type", "parent", "system_code", "is_postable", "is_active", "is_deleted")
    list_filter = ("account_type__category", "is_postable", "is_active", "is_deleted")
    search_fields = ("code", "name", "system_code")
    autocomplete_fields = ("account_type", "parent", "currency")


class JournalEntryLineInline(admin.TabularInline):
    model = JournalEntryLine
    extra = 2
    autocomplete_fields = ("account",)


@admin.register(JournalEntry)
class JournalEntryAdmin(SimpleHistoryAdmin):
    list_display = ("reference", "entry_date", "status", "source", "posted_at", "posted_by", "memo")
    list_filter = ("status", "source", "entry_date")
    search_fields = ("reference", "memo")
    inlines = [JournalEntryLineInline]
    readonly_fields = ("posted_at", "posted_by")


@admin.register(BankAccount)
class BankAccountAdmin(SimpleHistoryAdmin):
    list_display = ("name", "kind", "currency", "ledger_account", "is_active", "is_deleted")
    list_filter = ("kind", "is_active", "is_deleted")
    search_fields = ("name", "account_number", "mobile_number")
    autocomplete_fields = ("currency", "ledger_account")
