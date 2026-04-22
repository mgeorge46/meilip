"""Celery tasks for notification delivery + business-event helpers.

All outbound-HTTP tasks follow the SPEC §18 retry policy:
    autoretry_for=(httpx.HTTPError,)
    retry_backoff=True
    retry_backoff_max=600
    max_retries=5
"""
from __future__ import annotations

import logging

import httpx
from celery import shared_task

from .models import DeliveryStatus, NotificationDelivery, Template

logger = logging.getLogger(__name__)


RETRY_KW = dict(
    autoretry_for=(httpx.HTTPError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)


# ---------------------------------------------------------------------------
# Core delivery task
# ---------------------------------------------------------------------------
@shared_task(bind=True, **RETRY_KW)
def deliver_notification(self, delivery_id: int):
    """Send a single `NotificationDelivery` via its configured provider.

    On transient HTTP errors the task raises so Celery retries with
    exponential backoff. After `max_retries` the row stays FAILED.
    """
    try:
        delivery = NotificationDelivery.objects.get(pk=delivery_id)
    except NotificationDelivery.DoesNotExist:
        logger.warning("deliver_notification: row %s missing", delivery_id)
        return
    if delivery.status in (DeliveryStatus.SENT, DeliveryStatus.SKIPPED):
        return

    from .providers import get_provider
    provider = get_provider(delivery.channel)

    delivery.status = DeliveryStatus.SENDING
    delivery.attempt_count = (delivery.attempt_count or 0) + 1
    delivery.save(update_fields=["status", "attempt_count"])

    try:
        result = provider.send(delivery)
    except httpx.HTTPError as exc:
        delivery.mark_failed(
            error_detail=f"{type(exc).__name__}: {exc}",
            response=getattr(getattr(exc, "response", None), "text", None) or {},
        )
        raise  # let Celery retry
    except Exception as exc:  # Non-retryable
        delivery.mark_failed(error_detail=f"{type(exc).__name__}: {exc}")
        return

    delivery.mark_sent(
        provider=result.get("provider", provider.name),
        provider_message_id=result.get("message_id", ""),
        response=result.get("raw", {}),
    )


# ---------------------------------------------------------------------------
# Business-event helpers — thin wrappers callable from services / signals.
# ---------------------------------------------------------------------------
@shared_task
def send_payment_confirmation(payment_id: int):
    from billing.models import Payment
    from .services import enqueue_notification

    try:
        payment = Payment.objects.select_related("tenant").get(pk=payment_id)
    except Payment.DoesNotExist:
        return
    receipt = payment.receipts.first()
    enqueue_notification(
        template=Template.PAYMENT_CONFIRMATION,
        tenant=payment.tenant,
        context={
            "tenant_name": payment.tenant.full_name,
            "amount": int(payment.amount),
            "receipt_number": receipt.number if receipt else "",
            "received_at": payment.received_at.strftime("%Y-%m-%d %H:%M"),
        },
    )


@shared_task
def send_receipt(receipt_id: int):
    from billing.models import Receipt
    from .services import enqueue_notification

    try:
        receipt = Receipt.objects.select_related("payment__tenant", "refund__tenant").get(pk=receipt_id)
    except Receipt.DoesNotExist:
        return
    tenant = receipt.payment.tenant if receipt.payment_id else (
        receipt.refund.tenant if receipt.refund_id else None
    )
    if tenant is None:
        return
    enqueue_notification(
        template=Template.RECEIPT,
        tenant=tenant,
        context={
            "receipt_number": receipt.number,
            "amount": int(receipt.amount),
            "received_at": receipt.issued_at.strftime("%Y-%m-%d %H:%M"),
        },
    )


@shared_task
def send_overdue_reminder(invoice_id: int):
    from billing.models import Invoice
    from .services import enqueue_notification

    try:
        invoice = Invoice.objects.select_related("tenant_house__tenant").get(pk=invoice_id)
    except Invoice.DoesNotExist:
        return
    tenant = invoice.tenant_house.tenant
    enqueue_notification(
        template=Template.OVERDUE_REMINDER,
        tenant=tenant,
        context={
            "tenant_name": tenant.full_name,
            "invoice_number": invoice.number or str(invoice.pk),
            "outstanding": int(invoice.outstanding),
            "due_date": invoice.due_date.isoformat(),
        },
    )


@shared_task
def send_statement(landlord_id: int, period_label: str, net_amount: int):
    """Landlord statement notification — body content is assumed pre-rendered
    and attached out-of-band (PDF link in `context`)."""
    from core.models import Landlord
    from .services import enqueue_notification

    try:
        landlord = Landlord.objects.get(pk=landlord_id)
    except Landlord.DoesNotExist:
        return
    enqueue_notification(
        template=Template.STATEMENT,
        landlord=landlord,
        context={
            "period": period_label,
            "net_amount": int(net_amount),
            "landlord_name": landlord.full_name,
        },
    )


# ---------------------------------------------------------------------------
# Scheduled sweeper — retries rows stuck in QUEUED because the broker was
# down when they were created. Wired from celery beat (optional).
# ---------------------------------------------------------------------------
@shared_task
def sweep_queued_notifications(max_rows: int = 200):
    qs = NotificationDelivery.objects.filter(status=DeliveryStatus.QUEUED)[:max_rows]
    for row in qs:
        deliver_notification.delay(row.pk)
    return qs.count()
