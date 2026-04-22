from django.contrib import admin

from .models import NotificationDelivery


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "template", "channel", "recipient", "status",
        "attempt_count", "created_at", "sent_at",
    )
    list_filter = ("status", "channel", "template")
    search_fields = ("recipient", "provider_message_id")
    readonly_fields = [f.name for f in NotificationDelivery._meta.fields]
