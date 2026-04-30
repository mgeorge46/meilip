"""Cascade INACTIVE flags from Landlord / Estate / House down to TenantHouse
billing schedules.

Rules:
  - Landlord.status = INACTIVE  → every house under every estate of that
    landlord (and every house with that landlord as direct override) gets
    its active tenancy's `invoice_generation_status` set to STOPPED.
  - Estate.is_active = False    → every house under that estate gets the
    same treatment.
  - House.is_active = False     → that house's active tenancy stops.

Idempotent: only flips ACTIVE → STOPPED, leaves PAUSED/STOPPED alone.
Re-activating the parent does NOT auto-resume billing — that's a deliberate
finance decision and should be done per-tenancy from the tenant profile.
"""
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone


def _stop_billing(tenancies_qs, *, reason: str):
    """Flip ACTIVE invoice generation to STOPPED with an audit note."""
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    note = f"[{stamp}] Auto-stopped: {reason}."
    n = 0
    for th in tenancies_qs:
        if th.invoice_generation_status == th.InvoiceGenerationStatus.ACTIVE:
            th.invoice_generation_status = th.InvoiceGenerationStatus.STOPPED
            th.invoice_generation_note = (
                (th.invoice_generation_note + "\n" if th.invoice_generation_note else "")
                + note
            )[-1000:]
            th.save(update_fields=[
                "invoice_generation_status", "invoice_generation_note", "updated_at",
            ])
            n += 1
    return n


@receiver(pre_save, sender="core.Landlord")
def landlord_status_cascade(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        prev = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    if prev.status == instance.status:
        return
    if instance.status == instance.Status.INACTIVE:
        from .models import House, TenantHouse
        houses = House.objects.filter(
            models.Q(landlord=instance) | models.Q(estate__landlord=instance)
        )
        ths = TenantHouse.objects.filter(
            house__in=houses, status=TenantHouse.Status.ACTIVE,
        )
        _stop_billing(ths, reason=f"landlord {instance.full_name} marked INACTIVE")


@receiver(pre_save, sender="core.Estate")
def estate_active_cascade(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        prev = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    if prev.is_active == instance.is_active:
        return
    if not instance.is_active:
        from .models import TenantHouse
        ths = TenantHouse.objects.filter(
            house__estate=instance, status=TenantHouse.Status.ACTIVE,
        )
        _stop_billing(ths, reason=f"estate '{instance.name}' marked inactive")


@receiver(pre_save, sender="core.House")
def house_active_cascade(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        prev = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    if prev.is_active == instance.is_active:
        return
    if not instance.is_active:
        from .models import TenantHouse
        ths = TenantHouse.objects.filter(
            house=instance, status=TenantHouse.Status.ACTIVE,
        )
        _stop_billing(ths, reason=f"house '{instance}' marked inactive")


# Imported here to avoid circular imports — signals_inactive is loaded last.
from django.db import models  # noqa: E402
