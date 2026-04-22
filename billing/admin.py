"""Admin registrations for billing (read-mostly; state changes go through
views/services so guards fire)."""
from django.contrib import admin

from .models import (
    AdHocCharge,
    CommissionPosting,
    CreditNote,
    ExitSettlement,
    Invoice,
    InvoiceLine,
    InvoiceTaxLine,
    InvoiceVoid,
    Payment,
    PaymentAllocation,
    Receipt,
    Refund,
    SecurityDeposit,
    SecurityDepositMovement,
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


@admin.register(SecurityDeposit)
class SecurityDepositAdmin(admin.ModelAdmin):
    list_display = (
        "tenant_house", "amount_held", "amount_applied",
        "amount_refunded", "status",
    )
    list_filter = ("status",)
    readonly_fields = ("amount_held", "amount_applied", "amount_refunded", "status")


@admin.register(SecurityDepositMovement)
class SecurityDepositMovementAdmin(admin.ModelAdmin):
    list_display = ("deposit", "kind", "amount", "occurred_at")
    list_filter = ("kind",)


@admin.register(ExitSettlement)
class ExitSettlementAdmin(admin.ModelAdmin):
    list_display = (
        "tenant_house", "status", "approval_status",
        "final_refund_amount", "landlord_shortfall", "executed_at",
    )
    list_filter = ("status", "approval_status")
    readonly_fields = (
        "held_managed_at_start", "held_meili_at_start", "deposit_at_start",
        "outstanding_at_start", "damages_total", "plan",
        "final_refund_amount", "landlord_shortfall",
        "executed_at", "executed_by",
    )
