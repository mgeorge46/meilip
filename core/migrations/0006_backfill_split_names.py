"""Backfill first_name / last_name / other_names from existing full_name.

Splits using a simple heuristic:
- One word       -> first_name = word, last_name = ""
- Two words      -> first_name = W0, last_name = W1
- Three or more  -> first_name = W0, last_name = Wn, other_names = W1..Wn-1

Unused for historical rows; historical tables are left untouched (snapshots
should reflect the value at that point in time).
"""

from django.db import migrations


def _split(full):
    parts = (full or "").strip().split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[-1], " ".join(parts[1:-1])


def backfill(apps, schema_editor):
    for model_label in ("core.Landlord", "core.Tenant", "core.Employee"):
        app_label, model_name = model_label.split(".")
        Model = apps.get_model(app_label, model_name)
        for obj in Model.objects.all():
            if obj.first_name or obj.last_name or obj.other_names:
                continue
            first, last, other = _split(obj.full_name)
            obj.first_name = first
            obj.last_name = last
            obj.other_names = other
            obj.save(update_fields=["first_name", "last_name", "other_names"])


def noop_reverse(apps, schema_editor):
    # Reverse = leave name parts as-is; full_name still exists.
    pass


class Migration(migrations.Migration):
    dependencies = [("core", "0005_remove_employee_nssf_employee_and_more")]
    operations = [migrations.RunPython(backfill, noop_reverse)]
