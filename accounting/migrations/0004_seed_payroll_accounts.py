"""Seed payroll-specific Chart of Accounts (SPEC §14.2 / Phase 5 payroll).

Adds:
- Assets:   Staff Advances Receivable (6100)
- Liabilities: Salaries Payable, PAYE Payable, NSSF Payable, Other Payroll Payables
- Expenses: Salaries & Wages, Staff Allowances, NSSF Employer Expense

All amounts recorded on these accounts are UGX. System codes are the stable
identifiers used from code (`SALARIES_EXPENSE`, `PAYE_PAYABLE`, etc.).
"""

from django.db import migrations


# (code, name, account_type_code, parent_code, system_code, is_postable, description)
PAYROLL_ACCOUNTS = [
    # --- Asset ---------------------------------------------------------------
    ("1600", "Staff Advances Receivable", "ASSET", "1000", "STAFF_ADVANCES_RECEIVABLE", True,
        "Salary advances / loans to employees (recoverable from payroll)."),

    # --- Liabilities: Payroll payables (parent + leaves) ---------------------
    ("2500", "Payroll Payables", "LIABILITY", "2000", None, False,
        "Parent — all payroll-related short-term payables."),
    ("2510", "Salaries Payable", "LIABILITY", "2500", "SALARIES_PAYABLE", True,
        "Net salaries owed to employees pending bank transfer."),
    ("2520", "PAYE Payable", "LIABILITY", "2500", "PAYE_PAYABLE", True,
        "PAYE withheld from employees, owed to URA."),
    ("2530", "NSSF Payable", "LIABILITY", "2500", "NSSF_PAYABLE", True,
        "NSSF (employee + employer 5% + 10%) owed to NSSF."),
    ("2540", "Other Payroll Deductions Payable", "LIABILITY", "2500", "OTHER_PAYROLL_PAYABLE", True,
        "Garnishments, SACCO, union dues, etc."),

    # --- Expenses: Payroll (parent + leaves) ---------------------------------
    ("5400", "Payroll Expenses", "EXPENSE", "5000", None, False,
        "Parent — all payroll-related expense."),
    ("5410", "Salaries & Wages Expense", "EXPENSE", "5400", "SALARIES_EXPENSE", True,
        "Gross base salaries."),
    ("5420", "Staff Allowances Expense", "EXPENSE", "5400", "ALLOWANCES_EXPENSE", True,
        "Transport / housing / airtime / other allowances."),
    ("5430", "NSSF Employer Contribution Expense", "EXPENSE", "5400", "NSSF_EMPLOYER_EXPENSE", True,
        "10% employer-side NSSF contribution."),
]


def seed(apps, schema_editor):
    AccountType = apps.get_model("accounting", "AccountType")
    Account = apps.get_model("accounting", "Account")
    type_by_code = {t.code: t for t in AccountType.objects.all()}

    code_to_obj = {a.code: a for a in Account.objects.filter(code__in=["1000", "2000", "5000"])}

    # First pass — create without parents
    for code, name, type_code, _parent, system_code, is_postable, description in PAYROLL_ACCOUNTS:
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

    # Second pass — wire parents
    for code, _name, _type_code, parent_code, *_rest in PAYROLL_ACCOUNTS:
        if parent_code:
            obj = code_to_obj[code]
            obj.parent = code_to_obj[parent_code]
            obj.save(update_fields=["parent", "updated_at"])


def unseed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(code__in=[c for c, *_ in PAYROLL_ACCOUNTS]).delete()


class Migration(migrations.Migration):
    dependencies = [("accounting", "0003_alter_historicaljournalentry_reference_and_more")]
    operations = [migrations.RunPython(seed, unseed)]
