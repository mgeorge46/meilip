"""Seed default Chart of Accounts per SPEC §14.2.

Critical invariants:
- `Commission Income` is a standalone Revenue account — NOT merged with Rent Income.
- Two distinct advance-holding Liability accounts:
    * `Tenant Advance Payments Held — Managed Properties` (fiduciary)
    * `Tenant Advance Payments Held — Meili-Owned` (deferred revenue)
  Do NOT merge.
"""

from django.db import migrations


ACCOUNT_TYPES = [
    # (code, name, category, normal_balance, ordering)
    ("ASSET", "Assets", "ASSET", "DEBIT", 10),
    ("LIABILITY", "Liabilities", "LIABILITY", "CREDIT", 20),
    ("EQUITY", "Equity", "EQUITY", "CREDIT", 30),
    ("REVENUE", "Revenue", "REVENUE", "CREDIT", 40),
    ("EXPENSE", "Expenses", "EXPENSE", "DEBIT", 50),
]

# (code, name, account_type_code, parent_code, system_code, is_postable, description)
ACCOUNTS = [
    # --- Assets -------------------------------------------------------------
    ("1000", "Assets", "ASSET", None, None, False, "Parent — all assets"),
    ("1100", "Cash on Hand", "ASSET", "1000", "CASH_ON_HAND", True, ""),
    ("1200", "Bank Accounts", "ASSET", "1000", "BANK_ACCOUNTS", False, "Parent — individual bank accounts roll up here"),
    ("1300", "Mobile Money", "ASSET", "1000", "MOBILE_MONEY", False, "Parent — mobile money floats roll up here"),
    ("1400", "Accounts Receivable — Tenant Balances", "ASSET", "1000", "AR_TENANT_BALANCES", True, ""),
    ("1500", "Security Deposits Held", "ASSET", "1000", "SECURITY_DEPOSIT_HELD", True, "Cash side of deposits received from tenants"),

    # --- Liabilities --------------------------------------------------------
    ("2000", "Liabilities", "LIABILITY", None, None, False, "Parent — all liabilities"),
    ("2100", "Landlord Payable", "LIABILITY", "2000", "LANDLORD_PAYABLE", True, "Amounts owed to landlords on managed properties"),
    ("2200", "Security Deposits Refundable", "LIABILITY", "2000", "SECURITY_DEPOSIT_REFUNDABLE", True, "Deposits owed back to tenants"),
    ("2300", "Tax Payable", "LIABILITY", "2000", "TAX_PAYABLE", True, "VAT / Withholding owed to URA"),
    # --- TWO distinct advance-holding accounts — SPEC §20 ------------------
    ("2410", "Tenant Advance Payments Held — Managed Properties", "LIABILITY", "2000",
        "TENANT_ADVANCE_HELD_MANAGED", True,
        "Fiduciary — money held on behalf of external landlords. Never merge with 2420."),
    ("2420", "Tenant Advance Payments Held — Meili-Owned", "LIABILITY", "2000",
        "TENANT_ADVANCE_HELD_MEILI", True,
        "Deferred revenue on Meili-owned properties. Never merge with 2410."),

    # --- Equity -------------------------------------------------------------
    ("3000", "Equity", "EQUITY", None, None, False, "Parent — equity"),
    ("3100", "Owner's Equity", "EQUITY", "3000", "OWNERS_EQUITY", True, ""),
    ("3200", "Retained Earnings", "EQUITY", "3000", "RETAINED_EARNINGS", True, ""),

    # --- Revenue ------------------------------------------------------------
    ("4000", "Revenue", "REVENUE", None, None, False, "Parent — revenue"),
    ("4100", "Rent Income", "REVENUE", "4000", "RENT_INCOME", True,
        "Meili-owned properties only — 100% rent recognised as Meili revenue."),
    ("4200", "Commission Income", "REVENUE", "4000", "COMMISSION_INCOME", True,
        "Standalone — managed-property commission earnings. Never merged with Rent Income."),

    # --- Expenses -----------------------------------------------------------
    ("5000", "Expenses", "EXPENSE", None, None, False, "Parent — expenses"),
    ("5100", "Maintenance & Repairs", "EXPENSE", "5000", "MAINTENANCE_REPAIRS", True, ""),
    ("5200", "Office Supplies", "EXPENSE", "5000", "OFFICE_SUPPLIES", True, ""),
    ("5300", "Service Costs", "EXPENSE", "5000", "SERVICE_COSTS", True, ""),
]


def seed(apps, schema_editor):
    AccountType = apps.get_model("accounting", "AccountType")
    Account = apps.get_model("accounting", "Account")

    type_by_code = {}
    for code, name, category, normal, ordering in ACCOUNT_TYPES:
        obj, _ = AccountType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "category": category,
                "normal_balance": normal,
                "ordering": ordering,
            },
        )
        type_by_code[code] = obj

    code_to_obj = {}
    # First pass — create without parents
    for code, name, type_code, _parent, system_code, is_postable, description in ACCOUNTS:
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

    # Second pass — wire up parents
    for code, _name, _type_code, parent_code, *_rest in ACCOUNTS:
        if parent_code:
            obj = code_to_obj[code]
            obj.parent = code_to_obj[parent_code]
            obj.save(update_fields=["parent", "updated_at"])


def unseed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    AccountType = apps.get_model("accounting", "AccountType")
    Account.objects.filter(code__in=[c for c, *_ in ACCOUNTS]).delete()
    AccountType.objects.filter(code__in=[c for c, *_ in ACCOUNT_TYPES]).delete()


class Migration(migrations.Migration):
    dependencies = [("accounting", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
