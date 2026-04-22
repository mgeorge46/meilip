"""Tenant scoring models.

`TenantScore` — one row per tenant, overwritten by the daily Celery job.
Historical scores are preserved via `django-simple-history`. The score
spans all of a tenant's tenancies (weighted across houses) so a tenant
with one perfect house and one in arrears doesn't get a free pass.

Scores are **internal only** — never exposed on the tenant portal nor on
the landlord statement. See views (role gating) + templates (no render).
"""
from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from core.models import CoreBaseModel

from .tiers import Tier, tier_for_score


class TenantScore(CoreBaseModel):
    tenant = models.OneToOneField(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="score",
    )
    score = models.PositiveSmallIntegerField(
        default=0,
        help_text="0-100. Higher is better.",
    )
    tier = models.CharField(
        max_length=16, choices=Tier.choices, default=Tier.WATCH,
    )
    # Breakdown is a JSON blob so we can evolve the scoring algorithm
    # without a migration each time. Shape documented in
    # `scoring.services._compute_breakdown`.
    breakdown = models.JSONField(default=dict, blank=True)
    calculated_at = models.DateTimeField(auto_now=True)
    calculated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-score", "tenant__full_name"]
        indexes = [
            models.Index(fields=["tier"]),
            models.Index(fields=["score"]),
        ]

    def __str__(self):
        return f"{self.tenant} — {self.score} ({self.tier})"

    def save(self, *args, **kwargs):
        # Keep tier in sync with score, always.
        self.tier = tier_for_score(self.score)
        super().save(*args, **kwargs)
