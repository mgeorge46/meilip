"""Portal models — tenant + landlord-facing artefacts.

`LandlordStatement` persists every generated statement so Flower / the
accounting team can trace delivery. The PDF itself is stored under
MEDIA_ROOT / landlord_statements / YYYY / MM / .
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class LandlordStatement(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        GENERATED = "GENERATED", "Generated"
        DELIVERED = "DELIVERED", "Delivered"
        FAILED = "FAILED", "Failed"

    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        BOTH = "BOTH", "Both"
        MANUAL_DOWNLOAD = "MANUAL_DOWNLOAD", "Manual Download"

    landlord = models.ForeignKey(
        "core.Landlord", on_delete=models.CASCADE, related_name="statements"
    )
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    channel = models.CharField(max_length=16, choices=Channel.choices, blank=True)
    pdf = models.FileField(upload_to="landlord_statements/%Y/%m/", null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="landlord_statements_requested",
    )
    generated_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_notes = models.CharField(max_length=255, blank=True)
    # Cache totals so list/detail pages don't re-aggregate the ledger.
    total_cost = models.BigIntegerField(default=0)
    total_paid = models.BigIntegerField(default=0)
    total_balance = models.BigIntegerField(default=0)
    commission_amount = models.BigIntegerField(default=0)
    landlord_net = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-period_end", "-id"]
        indexes = [
            models.Index(fields=["landlord", "-period_end"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["landlord", "period_start", "period_end"],
                name="landlord_statement_unique_period",
            ),
        ]

    def __str__(self):
        return f"Statement — {self.landlord.full_name} {self.period_start}..{self.period_end}"

    @property
    def period_label(self):
        return self.period_start.strftime("%b-%y")
