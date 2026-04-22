"""Provider registry.

A provider is any callable with signature:

    send(delivery: NotificationDelivery) -> dict

Returning a provider-specific response dict on success, or raising an
exception (ideally `httpx.HTTPError`) on failure so Celery retry kicks in.
"""
from __future__ import annotations

from django.conf import settings

from ..models import Channel
from .africas_talking import AfricasTalkingSMSProvider, AfricasTalkingWhatsAppProvider
from .email_provider import DjangoEmailProvider


def get_provider(channel: str):
    """Return the concrete provider for a channel, honouring settings overrides."""
    override_map = getattr(settings, "NOTIFICATION_PROVIDERS", {}) or {}
    concrete = override_map.get(channel)
    if concrete == "console":
        from .console import ConsoleProvider
        return ConsoleProvider(channel)
    if channel == Channel.SMS:
        return AfricasTalkingSMSProvider()
    if channel == Channel.WHATSAPP:
        return AfricasTalkingWhatsAppProvider()
    if channel == Channel.EMAIL:
        return DjangoEmailProvider()
    raise ValueError(f"Unknown notification channel: {channel}")
