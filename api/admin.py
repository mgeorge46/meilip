from django.contrib import admin

from .models import ApiKey, WebhookEvent


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "key_prefix", "bank_account", "is_active", "last_used_at", "revoked_at")
    list_filter = ("is_active",)
    search_fields = ("name", "key_prefix")
    readonly_fields = ("key_prefix", "hashed_key", "last_used_at", "created_at")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("transaction_id", "api_key", "status", "response_code", "received_at")
    list_filter = ("status", "api_key")
    search_fields = ("transaction_id", "source_name")
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]
