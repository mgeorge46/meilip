"""Inbound payment webhook — `POST /api/v1/payments/`.

Pipeline:
    1. ApiKeyAuthentication validates the header + IP allowlist.
    2. Rate-limit (per-API-key) via django-ratelimit.
    3. Validate payload shape (PaymentWebhookSerializer).
    4. Idempotency — if (api_key, transaction_id) already exists, return the
       stored response untouched.
    5. Match the payer_reference to a Tenant; if no match, record UNMATCHED
       and return 202 with {status: "unmatched"}.
    6. Create Payment → apply_payment (FIFO + advance routing) → Receipt is
       created inside apply_payment.
    7. Enqueue notification (`send_payment_confirmation`).
    8. Store the outcome on WebhookEvent and return 201 with
       {status, receipt_number, payment_id}.
"""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiResponse

from .authentication import ApiKeyAuthentication, _client_ip
from .models import WebhookEvent
from .serializers import NotificationSendSerializer, PaymentWebhookSerializer
from .services import ingest_webhook_payment, match_tenant

logger = logging.getLogger(__name__)


class HasApiKey(BasePermission):
    """Authentication populated `request.auth` with the ApiKey row."""

    def has_permission(self, request, view):
        return request.auth is not None


def _rate_key(group, request):
    """Rate-limit per API key, not per IP — a single bank may front many IPs."""
    auth = getattr(request, "auth", None)
    return f"apikey:{auth.pk}" if auth else (request.META.get("REMOTE_ADDR") or "anon")


@method_decorator(
    ratelimit(key=_rate_key, rate="60/m", method="POST", block=True),
    name="dispatch",
)
class PaymentWebhookView(APIView):
    """SPEC §12.2 — receive a bank/MoMo notification, match, allocate, respond."""

    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [HasApiKey]

    @extend_schema(
        request=PaymentWebhookSerializer,
        responses={
            201: OpenApiResponse(description="Payment accepted and allocated."),
            202: OpenApiResponse(description="Duplicate or unmatched — logged, no double-credit."),
            400: OpenApiResponse(description="Malformed payload."),
            401: OpenApiResponse(description="Missing or invalid API key."),
            429: OpenApiResponse(description="Rate limit exceeded."),
        },
        summary="Record an external payment notification",
        description="Idempotent by (api_key, transaction_id). Matching is phone → id_number → prior-ref.",
    )
    def post(self, request):
        api_key = request.auth
        ip = _client_ip(request)

        serializer = PaymentWebhookSerializer(data=request.data)
        if not serializer.is_valid():
            WebhookEvent.objects.create(
                api_key=api_key,
                transaction_id=str(request.data.get("transaction_id", "")) or "unknown",
                source_name=str(request.data.get("source_name", "")),
                source_ip=ip or None,
                payload=request.data if isinstance(request.data, dict) else {},
                status=WebhookEvent.Status.INVALID,
                response_code=400,
                response_body={"errors": serializer.errors},
                error_detail="validation failed",
            )
            return Response(
                {"status": "invalid", "errors": serializer.errors},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data

        # Idempotency — replay returns the stored response.
        existing = WebhookEvent.objects.filter(
            api_key=api_key, transaction_id=data["transaction_id"],
        ).first()
        if existing is not None:
            return Response(
                {**existing.response_body, "status": "duplicate",
                 "original_status": existing.status},
                status=http_status.HTTP_202_ACCEPTED,
            )

        tenant = match_tenant(data["payer_reference"])
        if tenant is None:
            event = WebhookEvent.objects.create(
                api_key=api_key,
                transaction_id=data["transaction_id"],
                source_name=data.get("source_name", ""),
                source_ip=ip or None,
                payload=request.data,
                status=WebhookEvent.Status.UNMATCHED,
                response_code=202,
                response_body={"status": "unmatched",
                               "payer_reference": data["payer_reference"]},
            )
            logger.info("Webhook unmatched payer: %s", event.transaction_id)
            return Response(event.response_body, status=http_status.HTTP_202_ACCEPTED)

        try:
            payment = ingest_webhook_payment(
                api_key=api_key,
                tenant=tenant,
                amount=data["amount"],
                bank_account=api_key.bank_account,
                reference_number=data.get("reference_number") or data["transaction_id"],
                received_at=data["timestamp"],
            )
        except ValidationError as exc:
            WebhookEvent.objects.create(
                api_key=api_key,
                transaction_id=data["transaction_id"],
                source_name=data.get("source_name", ""),
                source_ip=ip or None,
                payload=request.data,
                status=WebhookEvent.Status.ERROR,
                response_code=400,
                response_body={"status": "error", "detail": str(exc)},
                error_detail=str(exc),
            )
            return Response(
                {"status": "error", "detail": str(exc)},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        receipt = payment.receipts.first()
        body = {
            "status": "accepted",
            "receipt_number": receipt.number if receipt else None,
            "payment_id": payment.pk,
            "payment_number": payment.number,
            "applied_amount": str(payment.amount),
        }
        WebhookEvent.objects.create(
            api_key=api_key,
            transaction_id=data["transaction_id"],
            source_name=data.get("source_name", ""),
            source_ip=ip or None,
            payload=request.data,
            status=WebhookEvent.Status.ACCEPTED,
            response_code=201,
            response_body=body,
            payment=payment,
        )

        # Fire off the tenant-side confirmation. Best-effort — a broker
        # outage must not roll back the already-posted payment.
        try:
            from notifications.tasks import send_payment_confirmation
            send_payment_confirmation.delay(payment.pk)
        except Exception:  # noqa: BLE001 — notifications never block ingest
            logger.exception("Failed to enqueue payment confirmation")

        return Response(body, status=http_status.HTTP_201_CREATED)


@method_decorator(
    ratelimit(key=_rate_key, rate="120/m", method="POST", block=True),
    name="dispatch",
)
class NotificationSendView(APIView):
    """`POST /api/v1/notifications/` — programmatic notification send.

    Used by external systems (e.g. Meili HQ ops tooling) to trigger tenant
    SMS/WhatsApp/Email through the same provider stack as internal events.
    Returns the created `NotificationDelivery` row so callers can poll
    `GET /api/v1/notifications/{id}/` for delivery status.
    """

    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [HasApiKey]

    @extend_schema(
        request=NotificationSendSerializer,
        responses={
            201: OpenApiResponse(description="Notification queued."),
            202: OpenApiResponse(description="Idempotent replay."),
            400: OpenApiResponse(description="Invalid payload."),
            401: OpenApiResponse(description="Missing or invalid API key."),
            404: OpenApiResponse(description="Referenced tenant/landlord not found."),
            429: OpenApiResponse(description="Rate limit exceeded."),
        },
        summary="Queue an outbound notification",
    )
    def post(self, request):
        from core.models import Landlord, Tenant
        from notifications.models import NotificationDelivery
        from notifications.services import enqueue_notification

        serializer = NotificationSendSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "invalid", "errors": serializer.errors},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data

        idem = data.get("idempotency_key") or ""
        if idem:
            existing = NotificationDelivery.objects.filter(
                context__idempotency_key=idem,
            ).first()
            if existing is not None:
                return Response(
                    {
                        "status": "duplicate",
                        "delivery_id": existing.pk,
                        "delivery_status": existing.status,
                    },
                    status=http_status.HTTP_202_ACCEPTED,
                )

        tenant = None
        landlord = None
        if data.get("tenant_id"):
            try:
                tenant = Tenant.objects.get(pk=data["tenant_id"])
            except Tenant.DoesNotExist:
                return Response(
                    {"status": "not_found", "detail": "tenant_id not found"},
                    status=http_status.HTTP_404_NOT_FOUND,
                )
        if data.get("landlord_id"):
            try:
                landlord = Landlord.objects.get(pk=data["landlord_id"])
            except Landlord.DoesNotExist:
                return Response(
                    {"status": "not_found", "detail": "landlord_id not found"},
                    status=http_status.HTTP_404_NOT_FOUND,
                )

        context = dict(data.get("context") or {})
        if idem:
            context["idempotency_key"] = idem

        delivery = enqueue_notification(
            template=data["template"],
            context=context,
            tenant=tenant,
            landlord=landlord,
            channel=data.get("channel"),
            recipient=data.get("recipient") or None,
        )

        return Response(
            {
                "status": "queued",
                "delivery_id": delivery.pk,
                "delivery_status": delivery.status,
                "channel": delivery.channel,
                "recipient": delivery.recipient,
            },
            status=http_status.HTTP_201_CREATED,
        )


class NotificationStatusView(APIView):
    """`GET /api/v1/notifications/{id}/` — check delivery status."""

    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [HasApiKey]

    @extend_schema(
        responses={
            200: OpenApiResponse(description="Delivery row."),
            401: OpenApiResponse(description="Missing or invalid API key."),
            404: OpenApiResponse(description="Delivery not found."),
        },
        summary="Poll notification delivery status",
    )
    def get(self, request, pk):
        from notifications.models import NotificationDelivery

        try:
            delivery = NotificationDelivery.objects.get(pk=pk)
        except NotificationDelivery.DoesNotExist:
            return Response(
                {"detail": "Not found."},
                status=http_status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                "id": delivery.pk,
                "status": delivery.status,
                "channel": delivery.channel,
                "template": delivery.template,
                "recipient": delivery.recipient,
                "attempt_count": delivery.attempt_count,
                "provider": delivery.provider,
                "provider_message_id": delivery.provider_message_id,
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
                "error_detail": delivery.error_detail,
            },
            status=http_status.HTTP_200_OK,
        )
