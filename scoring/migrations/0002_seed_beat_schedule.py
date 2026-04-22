"""Seed django-celery-beat PeriodicTask for daily tenant scoring.

Runs every day at 02:00 Africa/Kampala (local time). Admin-editable.
"""
import json

from django.db import migrations


def seed_schedule(apps, schema_editor):
    try:
        CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="2",
        day_of_month="*",
        month_of_year="*",
        day_of_week="*",
        timezone="Africa/Kampala",
    )
    PeriodicTask.objects.update_or_create(
        name="scoring.calculate_tenant_scores",
        defaults={
            "crontab": schedule,
            "task": "scoring.calculate_tenant_scores",
            "enabled": True,
            "description": "Daily recompute of every tenant's credit score.",
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
        },
    )


def unseed_schedule(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return
    PeriodicTask.objects.filter(name="scoring.calculate_tenant_scores").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scoring", "0001_initial"),
        ("django_celery_beat", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(seed_schedule, unseed_schedule),
    ]
