from decimal import Decimal

from django.db import migrations


CURRENCIES = [
    {"code": "UGX", "name": "Ugandan Shilling", "symbol": "USh", "decimal_places": 0, "is_primary": True},
    {"code": "USD", "name": "United States Dollar", "symbol": "$", "decimal_places": 2, "is_primary": False},
]

BILLING_CYCLES = [
    ("Hourly", "HOUR", 1),
    ("Daily", "DAY", 1),
    ("Weekly", "WEEK", 1),
    ("Bi-Weekly", "WEEK", 2),
    ("Monthly", "MONTH", 1),
    ("Quarterly", "QUARTER", 1),
    ("Semi-Annual", "SEMI_ANNUAL", 1),
    ("Yearly", "YEAR", 1),
]

TAXES = [
    {"code": "VAT-18", "name": "Value Added Tax (18%)", "kind": "VAT", "rate": Decimal("18.000"), "is_active": False},
    {"code": "WHT-6", "name": "Withholding Tax (6%)", "kind": "WITHHOLDING", "rate": Decimal("6.000"), "is_active": False},
]


def seed(apps, schema_editor):
    Currency = apps.get_model("core", "Currency")
    BillingCycle = apps.get_model("core", "BillingCycle")
    TaxType = apps.get_model("core", "TaxType")

    for cur in CURRENCIES:
        Currency.objects.update_or_create(code=cur["code"], defaults=cur)

    for name, unit, count in BILLING_CYCLES:
        BillingCycle.objects.update_or_create(
            name=name, defaults={"unit": unit, "count": count, "is_active": True}
        )

    for tax in TAXES:
        TaxType.objects.update_or_create(code=tax["code"], defaults=tax)


def unseed(apps, schema_editor):
    Currency = apps.get_model("core", "Currency")
    BillingCycle = apps.get_model("core", "BillingCycle")
    TaxType = apps.get_model("core", "TaxType")
    Currency.objects.filter(code__in=[c["code"] for c in CURRENCIES]).delete()
    BillingCycle.objects.filter(name__in=[n for n, _, _ in BILLING_CYCLES]).delete()
    TaxType.objects.filter(code__in=[t["code"] for t in TAXES]).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
