"""Phase 6 COA delta:

1. Remove NSSF-related accounts (simplified payroll — NSSF no longer tracked):
   - 2530 NSSF Payable
   - 5430 NSSF Employer Contribution Expense

2. Seed separately-billed utility income accounts (parent 4000 Revenue):
   - 4310 Water Income
   - 4320 Garbage / Waste Income
   - 4330 Security Income
   - 4340 Electricity Income
   - 4390 Other Utility Income

These are used whenever an invoice line covers a utility flagged as
`<utility>_billed_separately = True` at estate or house level (house
override wins). Commission routing is unchanged — utility income on
managed properties flows through the same landlord-payable/commission
split as rent (see billing engine).
"""

from django.db import migrations


UTILITY_INCOME_ACCOUNTS = [
    # (code, name, account_type_code, parent_code, system_code, is_postable, description)
    ("4310", "Water Income", "REVENUE", "4000", "WATER_INCOME", True,
        "Separately-billed water charges (when water_billed_separately=True)."),
    ("4320", "Garbage / Waste Income", "REVENUE", "4000", "GARBAGE_INCOME", True,
        "Separately-billed waste / garbage charges."),
    ("4330", "Security Income", "REVENUE", "4000", "SECURITY_INCOME", True,
        "Separately-billed security / guard service charges."),
    ("4340", "Electricity Income", "REVENUE", "4000", "ELECTRICITY_INCOME", True,
        "Separately-billed electricity / power charges."),
    ("4390", "Other Utility Income", "REVENUE", "4000", "OTHER_UTILITY_INCOME", True,
        "Catch-all for 'other bills' flagged at estate/house level."),
]


NSSF_CODES = ["2530", "5430"]


def apply(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    AccountType = apps.get_model("accounting", "AccountType")

    # 1) Drop NSSF accounts if present (safe: Phase-5 seed only; no journal
    # entries posted against them yet because payroll posting isn't wired).
    Account.objects.filter(code__in=NSSF_CODES).delete()

    # 2) Seed utility income accounts — two-pass to mirror existing pattern.
    type_by_code = {t.code: t for t in AccountType.objects.all()}
    code_to_obj = {a.code: a for a in Account.objects.filter(code__in=["4000"])}

    for code, name, type_code, _parent, system_code, is_postable, description in UTILITY_INCOME_ACCOUNTS:
        obj, _ = Account.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "account_type": type_by_code[type_code],
                "system_code": system_code or None,
                "is_postable": is_postable,
                "description": description,
                "is_active": True,
            },
        )
        code_to_obj[code] = obj

    for code, _name, _type_code, parent_code, *_rest in UTILITY_INCOME_ACCOUNTS:
        if parent_code:
            obj = code_to_obj[code]
            obj.parent = code_to_obj[parent_code]
            obj.save(update_fields=["parent", "updated_at"])


def reverse(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    AccountType = apps.get_model("accounting", "AccountType")
    type_by_code = {t.code: t for t in AccountType.objects.all()}

    # Remove utility income accounts.
    Account.objects.filter(code__in=[c for c, *_ in UTILITY_INCOME_ACCOUNTS]).delete()

    # Re-add NSSF accounts (idempotent).
    parent_2500 = Account.objects.filter(code="2500").first()
    parent_5400 = Account.objects.filter(code="5400").first()
    if parent_2500 and "LIABILITY" in type_by_code:
        Account.objects.update_or_create(
            code="2530",
            defaults={
                "name": "NSSF Payable",
                "account_type": type_by_code["LIABILITY"],
                "system_code": "NSSF_PAYABLE",
                "is_postable": True,
                "parent": parent_2500,
                "description": "NSSF (employee + employer 5% + 10%) owed to NSSF.",
                "is_active": True,
            },
        )
    if parent_5400 and "EXPENSE" in type_by_code:
        Account.objects.update_or_create(
            code="5430",
            defaults={
                "name": "NSSF Employer Contribution Expense",
                "account_type": type_by_code["EXPENSE"],
                "system_code": "NSSF_EMPLOYER_EXPENSE",
                "is_postable": True,
                "parent": parent_5400,
                "description": "10% employer-side NSSF contribution.",
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("accounting", "0004_seed_payroll_accounts")]
    operations = [migrations.RunPython(apply, reverse)]
