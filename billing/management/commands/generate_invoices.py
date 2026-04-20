"""Manual invoice generation trigger — mirrors the hourly Celery task."""
from django.core.management.base import BaseCommand

from billing.services import generate_invoices_for_due_tenancies


class Command(BaseCommand):
    help = "Generate invoices for all active tenancies due for billing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--today", type=str, default=None,
            help="Override the 'today' date (YYYY-MM-DD) for back-dated runs.",
        )

    def handle(self, *args, **opts):
        today = None
        if opts.get("today"):
            from datetime import date
            today = date.fromisoformat(opts["today"])
        result = generate_invoices_for_due_tenancies(today=today)
        self.stdout.write(self.style.SUCCESS(
            f"Created {result['created']} / skipped {result['skipped']} / "
            f"paused {result['paused']} / errors {len(result['errors'])}"
        ))
        for err in result["errors"]:
            self.stdout.write(self.style.WARNING(
                f"  tenancy {err['tenancy_id']}: {err['error']}"
            ))
