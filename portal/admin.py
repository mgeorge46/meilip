from django.contrib import admin

from .models import LandlordStatement


@admin.register(LandlordStatement)
class LandlordStatementAdmin(admin.ModelAdmin):
    list_display = (
        "landlord", "period_start", "period_end", "status", "channel",
        "landlord_net", "generated_at", "delivered_at",
    )
    list_filter = ("status", "channel")
    search_fields = ("landlord__full_name",)
    autocomplete_fields = ("landlord", "requested_by")
    readonly_fields = (
        "generated_at", "delivered_at", "total_cost", "total_paid",
        "total_balance", "commission_amount", "landlord_net", "created_at",
    )
    date_hierarchy = "period_end"
