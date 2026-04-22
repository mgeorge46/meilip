"""DRF serializers for the inbound payment webhook."""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers


class PaymentWebhookSerializer(serializers.Serializer):
    """Schema for `POST /api/v1/payments/`.

    - `amount` — integer UGX (whole shillings only, per UGXField)
    - `payer_reference` — tenant identifier. Matched in priority order:
        phone (E.164 or local) → tenant.id_number → Payment.reference_number lookup
    - `transaction_id` — unique per (api_key); duplicates return the
        original receipt instead of double-crediting.
    - `timestamp` — ISO 8601 UTC of when the originating system booked it.
    - `source_name` — free-text label (e.g. "Stanbic USSD", "MTN MoMo").
    """

    amount = serializers.IntegerField(min_value=1)
    payer_reference = serializers.CharField(max_length=64)
    transaction_id = serializers.CharField(max_length=128)
    timestamp = serializers.DateTimeField()
    source_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    reference_number = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
        help_text="Originating system's internal reference.",
    )

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("amount must be positive.")
        return Decimal(value)


# ---------------------------------------------------------------------------
# Outbound notification webhook — `POST /api/v1/notifications/`
# ---------------------------------------------------------------------------
class NotificationSendSerializer(serializers.Serializer):
    """Schema for programmatic notification sends.

    At least one of `tenant_id`, `landlord_id` or `recipient` must be
    provided. When a tenant/landlord is given, the channel + destination
    are resolved from their profile preferences unless explicitly overridden.

    Templates: PAYMENT_CONFIRMATION | RECEIPT | OVERDUE_REMINDER | STATEMENT
               | GENERIC. GENERIC requires a `body` field in `context`.
    """

    template = serializers.ChoiceField(
        choices=[
            "PAYMENT_CONFIRMATION", "RECEIPT", "OVERDUE_REMINDER",
            "STATEMENT", "GENERIC",
        ],
    )
    tenant_id = serializers.IntegerField(required=False)
    landlord_id = serializers.IntegerField(required=False)
    recipient = serializers.CharField(max_length=160, required=False, allow_blank=True)
    channel = serializers.ChoiceField(
        choices=["SMS", "WHATSAPP", "EMAIL"], required=False,
    )
    context = serializers.DictField(required=False, default=dict)
    idempotency_key = serializers.CharField(
        max_length=128, required=False, allow_blank=True,
        help_text="Optional key — duplicate sends with the same key return the original row.",
    )

    def validate(self, data):
        if not (data.get("tenant_id") or data.get("landlord_id") or data.get("recipient")):
            raise serializers.ValidationError(
                "Must specify tenant_id, landlord_id, or a raw recipient."
            )
        if data["template"] == "GENERIC":
            body = (data.get("context") or {}).get("message") or (data.get("context") or {}).get("body")
            if not body:
                raise serializers.ValidationError(
                    {"context": "GENERIC template requires context.message"}
                )
        return data

