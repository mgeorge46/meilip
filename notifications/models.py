"""NotificationDelivery — one row per attempted outbound message.

Separate from Celery task state so we have a business-level audit trail
that survives task result pruning. Provider responses + retry count are
stored for operational visibility and dispute handling.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Channel(models.TextChoices):
    SMS = "SMS", "SMS"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    EMAIL = "EMAIL", "Email"


class DeliveryStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"


class Template(models.TextChoices):
    PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION", "Payment confirmation"
    RECEIPT = "RECEIPT", "Receipt"
    OVERDUE_REMINDER = "OVERDUE_REMINDER", "Overdue reminder"
    STATEMENT = "STATEMENT", "Landlord statement"
    GENERIC = "GENERIC", "Generic"


class NotificationDelivery(models.Model):
    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    landlord = models.ForeignKey(
        "core.Landlord", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    recipient = models.CharField(
        max_length=160,
        help_text="Resolved destination (phone, email, or WhatsApp ID).",
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    template = models.CharField(max_length=32, choices=Template.choices)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.QUEUED,
    )
    provider = models.CharField(max_length=40, blank=True)
    provider_message_id = models.CharField(max_length=120, blank=True)
    provider_response = models.JSONField(default=dict, blank=True)
    error_detail = models.TextField(blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["template", "status"]),
        ]

    def __str__(self):
        return f"{self.get_template_display()} → {self.recipient} [{self.status}]"

    def mark_sent(self, *, provider, provider_message_id="", response=None):
        self.status = DeliveryStatus.SENT
        self.provider = provider
        self.provider_message_id = provider_message_id or ""
        self.provider_response = response or {}
        self.sent_at = timezone.now()
        self.save(update_fields=[
            "status", "provider", "provider_message_id", "provider_response",
            "sent_at",
        ])

    def mark_failed(self, *, error_detail, response=None):
        self.status = DeliveryStatus.FAILED
        self.error_detail = str(error_detail)[:4000]
        if response is not None:
            self.provider_response = response
        self.save(update_fields=["status", "error_detail", "provider_response"])
