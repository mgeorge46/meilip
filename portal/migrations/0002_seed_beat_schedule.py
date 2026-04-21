"""Seed django-celery-beat PeriodicTask for monthly landlord statements.

Runs 1st of every month at 06:00 Africa/Kampala (local time).
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
        hour="6",
        day_of_month="1",
        month_of_year="*",
        day_of_week="*",
        timezone="Africa/Kampala",
    )
    PeriodicTask.objects.update_or_create(
        name="portal.schedule_monthly_statements",
        defaults={
            "crontab": schedule,
            "task": "portal.tasks.schedule_monthly_statements",
            "enabled": True,
            "description": "Generate + deliver monthly landlord statements.",
            "args": json.dumps([]),
            "kwargs": json.dumps({}),
        },
    )


def unseed_schedule(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return
    PeriodicTask.objects.filter(name="portal.schedule_monthly_statements").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0001_initial"),
        ("django_celery_beat", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(seed_schedule, unseed_schedule),
    ]
