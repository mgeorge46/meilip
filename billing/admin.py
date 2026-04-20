"""Admin registrations for billing (read-mostly; state changes go through
views/services so guards fire)."""
from django.contrib import admin

from .models import (
    AdHocCharge,
    CommissionPosting,
    CreditNote,
    Invoice,
    InvoiceLine,
    InvoiceTaxLine,
    InvoiceVoid,
    Payment,
    PaymentAllocation,
    Receipt,
    Refund,
)
from .sequences import NumberSequence


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "tenant_house", "period_from", "period_to", "total", "status")
    list_filter = ("status",)
    search_fields = ("number",)
    readonly_fields = ("number", "subtotal", "tax_total", "total", "status", "issued_at")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("number", "tenant", "amount", "method", "approval_status", "received_at")
    list_filter = ("approval_status", "method")
    search_fields = ("number", "reference_number")


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("number", "tenant", "amount", "method", "source", "approval_status")
    list_filter = ("approval_status", "source", "method")


@admin.register(CreditNote)
class CreditNoteAdmin(admin.ModelAdmin):
    list_display = ("number", "original_invoice", "amount", "approval_status", "is_voided")
    list_filter = ("approval_status", "reason_category")


@admin.register(InvoiceVoid)
class InvoiceVoidAdmin(admin.ModelAdmin):
    list_display = ("invoice", "reason_category", "approval_status", "void_date")
    list_filter = ("approval_status", "reason_category")


admin.site.register(InvoiceLine)
admin.site.register(InvoiceTaxLine)
admin.site.register(AdHocCharge)
admin.site.register(PaymentAllocation)
admin.site.register(Receipt)
admin.site.register(CommissionPosting)
admin.site.register(NumberSequence)
