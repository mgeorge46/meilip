"""Celery tasks for the portal app — landlord statement generation and delivery.

Two shared tasks:

* `generate_landlord_statement` — build context + render PDF + persist, then
  optionally chain `deliver_landlord_statement`. Called on-demand from the
  landlord portal and by the monthly Celery-beat schedule (1st @ 06:00
  Africa/Kampala) via `schedule_monthly_statements`.

* `deliver_landlord_statement` — reads the landlord's preferred channel and
  dispatches the PDF. Until the Phase 6 notification adapter lands this just
  logs the intent and marks the row DELIVERED so the workflow is end-to-end
  exercisable.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_landlord_statement(
    self,
    *,
    landlord_id: int,
    period_start_iso: str,
    period_end_iso: str,
    requested_by_id: int | None = None,
    deliver: bool = True,
):
    from core.models import Landlord

    from .models import LandlordStatement
    from .services import build_statement_context, persist_statement, render_statement_pdf

    landlord = Landlord.objects.get(pk=landlord_id)
    period_start = date.fromisoformat(period_start_iso)
    period_end = date.fromisoformat(period_end_iso)

    requested_by = None
    if requested_by_id:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        requested_by = User.objects.filter(pk=requested_by_id).first()

    try:
        ctx = build_statement_context(landlord, period_start, period_end)
        pdf_bytes = render_statement_pdf(ctx)
        stmt = persist_statement(
            landlord, period_start, period_end, pdf_bytes, ctx,
            requested_by=requested_by,
        )
    except Exception as exc:
        logger.exception("Statement generation failed for landlord=%s", landlord_id)
        try:
            stmt, _ = LandlordStatement.objects.update_or_create(
                landlord=landlord,
                period_start=period_start,
                period_end=period_end,
                defaults={
                    "status": LandlordStatement.Status.FAILED,
                    "delivery_notes": str(exc)[:255],
                    "requested_by": requested_by,
                },
            )
        except Exception:
            pass
        raise self.retry(exc=exc)

    if deliver:
        deliver_landlord_statement.delay(statement_id=stmt.pk)
    return stmt.pk


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def deliver_landlord_statement(self, *, statement_id: int):
    from core.models import Landlord

    from .models import LandlordStatement

    stmt = LandlordStatement.objects.select_related("landlord").get(pk=statement_id)
    landlord = stmt.landlord
    channel = landlord.preferred_statement_channel or Landlord.StatementChannel.EMAIL

    # Phase 6 will replace these stubs with real adapters (Email/WhatsApp).
    notes = []
    if channel in {Landlord.StatementChannel.EMAIL, Landlord.StatementChannel.BOTH}:
        if landlord.email:
            logger.info(
                "[stub-email] statement=%s to=%s period=%s..%s",
                stmt.pk, landlord.email, stmt.period_start, stmt.period_end,
            )
            notes.append(f"email->{landlord.email}")
        else:
            notes.append("email skipped (no address)")
    if channel in {Landlord.StatementChannel.WHATSAPP, Landlord.StatementChannel.BOTH}:
        whatsapp = landlord.whatsapp_number or landlord.phone
        if whatsapp:
            logger.info(
                "[stub-whatsapp] statement=%s to=%s period=%s..%s",
                stmt.pk, whatsapp, stmt.period_start, stmt.period_end,
            )
            notes.append(f"whatsapp->{whatsapp}")
        else:
            notes.append("whatsapp skipped (no number)")

    if channel == Landlord.StatementChannel.NONE:
        stmt.channel = LandlordStatement.Channel.MANUAL_DOWNLOAD
        stmt.delivery_notes = "auto-delivery disabled; available for manual download"
    else:
        stmt.channel = {
            Landlord.StatementChannel.EMAIL: LandlordStatement.Channel.EMAIL,
            Landlord.StatementChannel.WHATSAPP: LandlordStatement.Channel.WHATSAPP,
            Landlord.StatementChannel.BOTH: LandlordStatement.Channel.BOTH,
        }.get(channel, LandlordStatement.Channel.EMAIL)
        stmt.delivery_notes = "; ".join(notes)[:255]

    stmt.status = LandlordStatement.Status.DELIVERED
    stmt.delivered_at = timezone.now()
    stmt.save(update_fields=["channel", "delivery_notes", "status", "delivered_at"])
    return stmt.pk


@shared_task
def schedule_monthly_statements():
    """Beat-triggered task — runs 1st of each month at 06:00 Africa/Kampala.

    Produces a statement for the previous calendar month for every active
    landlord whose preferred channel is not NONE.
    """
    from core.models import Landlord

    today = timezone.localdate()
    # First of this month -> previous month bounds
    first_of_this_month = today.replace(day=1)
    period_end = first_of_this_month - timedelta(days=1)
    period_start = period_end.replace(day=1)

    qs = Landlord.objects.filter(
        status=Landlord.Status.ACTIVE,
        is_deleted=False,
    ).exclude(preferred_statement_channel=Landlord.StatementChannel.NONE)

    dispatched = 0
    for landlord in qs:
        generate_landlord_statement.delay(
            landlord_id=landlord.pk,
            period_start_iso=period_start.isoformat(),
            period_end_iso=period_end.isoformat(),
            requested_by_id=None,
            deliver=True,
        )
        dispatched += 1
    logger.info(
        "schedule_monthly_statements: dispatched=%s period=%s..%s",
        dispatched, period_start, period_end,
    )
    return dispatched
