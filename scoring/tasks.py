"""Celery tasks for tenant scoring.

Scheduled via django-celery-beat — see migration
`scoring/0002_seed_beat_schedule.py` for the default cron `0 2 * * *`
Africa/Kampala (daily 02:00). Editable from Django admin.
"""
from celery import shared_task

from .services import calculate_scores_for_all


@shared_task(name="scoring.calculate_tenant_scores")
def calculate_tenant_scores():
    """Daily sweep — recompute all tenant scores."""
    return calculate_scores_for_all()
