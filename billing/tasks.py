"""Celery tasks for billing.

Scheduled via django-celery-beat — see README for periodic task wiring:
    - generate_invoices: cron `0 * * * *` (hourly)
    - mark_overdue: cron `0 1 * * *` (01:00 Africa/Kampala)
"""
from celery import shared_task

from .services import generate_invoices_for_due_tenancies, mark_overdue_invoices


@shared_task(name="billing.generate_invoices")
def generate_invoices():
    """Hourly invoice generation sweep."""
    return generate_invoices_for_due_tenancies()


@shared_task(name="billing.mark_overdue")
def mark_overdue():
    """Daily 01:00 sweep — mark issued/partial invoices past due_date OVERDUE."""
    return {"transitioned": mark_overdue_invoices()}
