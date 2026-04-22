"""Manual tenant-scoring run — mirrors the daily Celery task.

Usage:
    python manage.py calculate_scores                    # all tenants
    python manage.py calculate_scores --tenant 42        # single tenant
    python manage.py calculate_scores --today 2026-04-22 # back-date
"""
from datetime import date

from django.core.management.base import BaseCommand

from scoring.services import calculate_scores_for_all, calculate_score_for_tenant
from core.models import Tenant


class Command(BaseCommand):
    help = "Recalculate tenant credit scores."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None,
                            help="Only score this tenant (PK).")
        parser.add_argument("--today", type=str, default=None,
                            help="Override the 'today' date (YYYY-MM-DD).")

    def handle(self, *args, **opts):
        today = date.fromisoformat(opts["today"]) if opts.get("today") else None
        if opts.get("tenant"):
            tenant = Tenant.objects.get(pk=opts["tenant"])
            row = calculate_score_for_tenant(tenant, today=today)
            self.stdout.write(self.style.SUCCESS(
                f"{tenant}: score={row.score} tier={row.tier}"
            ))
            return
        result = calculate_scores_for_all(today=today)
        self.stdout.write(self.style.SUCCESS(
            f"Processed {result['processed']} tenants / errors {len(result['errors'])}"
        ))
        for err in result["errors"]:
            self.stdout.write(self.style.WARNING(
                f"  tenant {err['tenant_id']}: {err['error']}"
            ))
