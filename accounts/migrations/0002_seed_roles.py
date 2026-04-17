from django.db import migrations


ROLES = [
    ("SUPER_ADMIN", "Super Admin — destructive actions, top-level administration"),
    ("ADMIN", "Admin — most administrative actions"),
    ("ACCOUNT_MANAGER", "Account Manager — routine invoicing, tenancy management"),
    ("COLLECTIONS", "Collections — payment entry, follow-up"),
    ("SALES_REP", "Sales Rep — read-only access to own tenants"),
    ("FINANCE", "Finance — voids, credit notes, refunds (with maker-checker)"),
    ("TENANT", "Tenant — portal self-service"),
    ("LANDLORD", "Landlord — portal self-service"),
]


def seed_roles(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for name, desc in ROLES:
        Role.objects.update_or_create(
            name=name,
            defaults={"description": desc, "is_system": True, "is_active": True},
        )


def unseed_roles(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(name__in=[n for n, _ in ROLES]).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(seed_roles, unseed_roles)]
