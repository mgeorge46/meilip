from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import TenantScore


@admin.register(TenantScore)
class TenantScoreAdmin(SimpleHistoryAdmin):
    list_display = ("tenant", "score", "tier", "calculated_at")
    list_filter = ("tier",)
    search_fields = ("tenant__full_name", "tenant__phone", "tenant__email")
    readonly_fields = (
        "tenant", "score", "tier", "breakdown", "calculated_at",
        "calculated_by", "created_at", "updated_at",
    )
    autocomplete_fields = ("tenant",)
