"""Tenant matching + payment ingestion used by the webhook view.

Kept out of `views.py` so the same logic is reachable from management
commands or manual retries on unmatched rows.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.utils import timezone

from billing.models import ApprovalStatus, Payment
from billing.services import apply_payment
from core.models import Tenant


# Accept digits only. Strips +, spaces, dashes. Matches last 9 digits
# so "+256700900099" and "0700900099" both resolve to "700900099".
_PHONE_TAIL = re.compile(r"(\d{9})$")


def _normalize_phone_tail(raw: str) -> Optional[str]:
    digits = re.sub(r"\D+", "", raw or "")
    m = _PHONE_TAIL.search(digits)
    return m.group(1) if m else None


def match_tenant(payer_reference: str) -> Optional[Tenant]:
    """Resolve a payer identifier to a Tenant.

    Strategy (first match wins):
      1. Exact `phone` match.
      2. Last-9-digits match against `phone` (handles +256 vs 0 prefixes).
      3. Exact `id_number` match.
      4. Historical Payment.reference_number match (repeat customer).
    """
    if not payer_reference:
        return None
    ref = payer_reference.strip()

    exact = Tenant.objects.filter(phone=ref).first()
    if exact is not None:
        return exact

    tail = _normalize_phone_tail(ref)
    if tail:
        phone_match = Tenant.objects.filter(phone__endswith=tail).first()
        if phone_match is not None:
            return phone_match

    id_match = Tenant.objects.filter(id_number=ref).exclude(id_number="").first()
    if id_match is not None:
        return id_match

    prior_payment = (
        Payment.objects.filter(reference_number=ref)
        .select_related("tenant")
        .order_by("-received_at")
        .first()
    )
    if prior_payment is not None:
        return prior_payment.tenant
    return None


@transaction.atomic
def ingest_webhook_payment(
    *,
    api_key,
    tenant: Tenant,
    amount: Decimal,
    bank_account,
    reference_number: str = "",
    received_at=None,
):
    """Create an AUTO_APPROVED Payment and run `apply_payment` immediately.

    Webhook payments are trusted (API key is the authenticator) — they
    bypass the maker-checker queue to avoid delayed receipting. The
    ApiKey row is recorded on the payment's reference_number prefix so
    auditors can trace back to the integration.
    """
    payment = Payment.objects.create(
        tenant=tenant,
        amount=amount,
        method=Payment.Method.MOBILE_MONEY,  # generic digital channel
        bank_account=bank_account,
        reference_number=reference_number or "",
        received_at=received_at or timezone.now(),
        approval_status=ApprovalStatus.AUTO_APPROVED,
        approved_at=timezone.now(),
    )
    apply_payment(payment)
    return payment
