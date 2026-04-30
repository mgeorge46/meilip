"""High-level helpers for enqueueing a notification.

Resolves the recipient (tenant/landlord → phone/email based on prefs),
renders a template, creates a NotificationDelivery row, and queues the
delivery Celery task.
"""
from __future__ import annotations

from typing import Optional

from django.template.loader import render_to_string
from django.utils import timezone

from .models import Channel, DeliveryStatus, NotificationDelivery, Template


_TEMPLATE_SUBJECTS = {
    Template.PAYMENT_CONFIRMATION: "Payment received",
    Template.RECEIPT: "Your receipt",
    Template.OVERDUE_REMINDER: "Rent overdue reminder",
    Template.STATEMENT: "Landlord statement",
    Template.PASSWORD_RESET: "Reset your Meili Property password",
    Template.ADMIN_PASSWORD: "Your Meili Property account",
    Template.GENERIC: "Meili Property notification",
}


def _resolve_tenant_channel(tenant) -> tuple[str, str]:
    """Return (channel, recipient) based on tenant's preference.

    Falls back to SMS/phone if the preferred channel has no destination set.
    """
    pref = tenant.preferred_notification
    if pref == "EMAIL" and tenant.email:
        return Channel.EMAIL, tenant.email
    if pref == "WHATSAPP" and tenant.phone:
        return Channel.WHATSAPP, tenant.phone
    return Channel.SMS, tenant.phone


def _render(template_name: str, context: dict) -> tuple[str, str]:
    """Return (subject, body) — subject uses the static map above, body is
    rendered from `notifications/<slug>.txt` if it exists; otherwise a
    plain-text fallback is composed from context."""
    subject = _TEMPLATE_SUBJECTS.get(template_name, "Meili Property")
    slug = template_name.lower()
    try:
        body = render_to_string(f"notifications/{slug}.txt", context).strip()
    except Exception:
        body = _fallback_body(template_name, context)
    return subject, body


def _fallback_body(template_name: str, ctx: dict) -> str:
    if template_name == Template.PAYMENT_CONFIRMATION:
        return (
            f"Hi {ctx.get('tenant_name', '')}, we received your payment of "
            f"UGX {ctx.get('amount', 0):,}. "
            f"Receipt: {ctx.get('receipt_number', '')}. Thank you."
        )
    if template_name == Template.RECEIPT:
        return (
            f"Receipt {ctx.get('receipt_number', '')}: "
            f"UGX {ctx.get('amount', 0):,} received on "
            f"{ctx.get('received_at', '')}."
        )
    if template_name == Template.OVERDUE_REMINDER:
        return (
            f"Rent invoice {ctx.get('invoice_number', '')} is overdue. "
            f"Amount due: UGX {ctx.get('outstanding', 0):,}. "
            f"Please pay by {ctx.get('due_date', 'as soon as possible')}."
        )
    if template_name == Template.STATEMENT:
        return (
            f"Statement {ctx.get('period', '')}: "
            f"Net UGX {ctx.get('net_amount', 0):,}."
        )
    if template_name == Template.PASSWORD_RESET:
        # Prefer the human-friendly `expires_in` (e.g. "30 minutes"); fall
        # back to `expiry_hours` for older callers.
        expires_in = ctx.get("expires_in") or f"{ctx.get('expiry_hours', 24)} hours"
        return (
            f"Hi {ctx.get('user_name', '')},\n\n"
            f"Someone (hopefully you) requested a password reset for your "
            f"Meili Property account ({ctx.get('email', '')}).\n\n"
            f"Click the link below to set a new password. It expires in "
            f"{expires_in}.\n\n"
            f"{ctx.get('reset_url', '')}\n\n"
            f"If you didn't request this, ignore this email — your password "
            f"won't change."
        )
    if template_name == Template.ADMIN_PASSWORD:
        return (
            f"Hi {ctx.get('user_name', '')},\n\n"
            f"An administrator has issued you a temporary password for your "
            f"Meili Property account ({ctx.get('email', '')}).\n\n"
            f"Temporary password: {ctx.get('temp_password', '')}\n\n"
            f"Sign in at {ctx.get('login_url', '')} — you'll be required to "
            f"set a new password before continuing."
        )
    return ctx.get("message", "Meili Property notification")


def enqueue_notification(
    *,
    template: str,
    context: dict,
    tenant=None,
    landlord=None,
    channel: Optional[str] = None,
    recipient: Optional[str] = None,
    user=None,
):
    """Create a `NotificationDelivery` row and dispatch the Celery task.

    At least one of (tenant, landlord, recipient) must be provided. When
    `channel` / `recipient` are omitted, they are resolved from the tenant's
    preference.
    """
    if tenant is None and landlord is None and not recipient:
        raise ValueError("Must specify a tenant, landlord, or raw recipient.")
    if channel is None or recipient is None:
        if tenant is not None:
            channel, recipient = _resolve_tenant_channel(tenant)
        elif landlord is not None:
            channel = Channel.EMAIL if landlord.email else Channel.SMS
            recipient = landlord.email or landlord.phone
    if not recipient:
        # Nothing to send to — record as SKIPPED for audit.
        delivery = NotificationDelivery.objects.create(
            tenant=tenant, landlord=landlord, recipient="",
            channel=channel or Channel.SMS, template=template,
            subject="", body="", context=context or {},
            status=DeliveryStatus.SKIPPED,
            error_detail="no recipient available",
            created_by=user,
        )
        return delivery

    subject, body = _render(template, context or {})
    delivery = NotificationDelivery.objects.create(
        tenant=tenant, landlord=landlord, recipient=recipient,
        channel=channel, template=template,
        subject=subject, body=body, context=context or {},
        status=DeliveryStatus.QUEUED, created_by=user,
    )

    # Dispatch to Celery. The task is idempotent (checks status first).
    try:
        from .tasks import deliver_notification
        deliver_notification.delay(delivery.pk)
    except Exception:
        # Broker unavailable — leave QUEUED for the scheduled sweeper to retry.
        pass
    return delivery
