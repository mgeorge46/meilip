"""Mint an ApiKey and print the raw value once.

Usage:
    py manage.py issue_api_key --name "Stanbic Webhook" --bank-account 3
"""
from django.core.management.base import BaseCommand, CommandError

from accounting.models import BankAccount
from api.models import ApiKey


class Command(BaseCommand):
    help = "Issue an API key for a bank/provider and print the raw value (stored hashed)."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--bank-account", type=int, required=True,
                            help="BankAccount.id that this key deposits to.")
        parser.add_argument("--allowed-ips", default="",
                            help="Comma-separated allowlist. Blank = any.")

    def handle(self, *args, **opts):
        try:
            ba = BankAccount.objects.get(pk=opts["bank_account"])
        except BankAccount.DoesNotExist:
            raise CommandError("BankAccount not found.")
        key, raw = ApiKey.issue(
            name=opts["name"], bank_account=ba,
            allowed_ips=opts["allowed_ips"],
        )
        self.stdout.write(self.style.SUCCESS(
            f"Issued key {key.pk} ({key.key_prefix}...) bound to {ba}."
        ))
        self.stdout.write(self.style.WARNING(
            "\nRaw key (shown once — store it securely):\n\n"
            f"    {raw}\n"
        ))
