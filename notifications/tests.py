"""Notification tests — provider selection, retry policy, message rendering,
and hook integration with billing services. Outbound HTTP is mocked using
httpx.MockTransport so tests never touch the network.
"""
from decimal import Decimal
from unittest import mock

import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounting.models import BankAccount
from accounting.utils import SYS_CASH, get_account
from billing.models import ApprovalStatus, Payment
from billing.services import apply_payment
from core.models import (
    BillingCycle,
    BillingMode,
    Currency,
    Estate,
    House,
    Landlord,
    Tenant,
    TenantHouse,
)

from .models import Channel, DeliveryStatus, NotificationDelivery, Template
from .services import _resolve_tenant_channel, enqueue_notification

User = get_user_model()


def _make_user(email):
    return User.objects.create_user(
        email=email,
        phone=f"+25670{abs(hash(email)) % 100_000_00:08d}",
        password="pw-long-enough-1",
        first_name="N", last_name="T",
    )


class NotifyFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ugx = Currency.objects.get(code="UGX")
        cls.cycle = BillingCycle.objects.get(name="Monthly")
        cls.landlord = Landlord.objects.create(
            full_name="LL", phone="+256700000010", is_meili_owned=True,
        )
        cls.estate = Estate.objects.create(
            landlord=cls.landlord, name="EN", currency=cls.ugx,
            billing_cycle=cls.cycle, billing_mode=BillingMode.PREPAID,
        )
        cls.house = House.objects.create(
            estate=cls.estate, house_number="N1", periodic_rent=Decimal("100000"),
        )
        cls.user = _make_user("n-tenant@meili.test")
        cls.tenant = Tenant.objects.create(
            user=cls.user, full_name="Notify Tenant",
            phone="+256700900050", email="notify@example.com",
        )
        TenantHouse.objects.create(
            tenant=cls.tenant, house=cls.house,
            status=TenantHouse.Status.ACTIVE,
            move_in_date=timezone.localdate(),
            billing_start_date=timezone.localdate(),
        )
        cls.bank = BankAccount.objects.create(
            name="Cash", kind=BankAccount.Kind.CASH,
            currency=cls.ugx, ledger_account=get_account(SYS_CASH),
        )


class ChannelResolutionTests(NotifyFixture):
    def test_default_sms_to_phone(self):
        self.tenant.preferred_notification = "SMS"
        self.tenant.save(update_fields=["preferred_notification"])
        channel, to = _resolve_tenant_channel(self.tenant)
        self.assertEqual(channel, Channel.SMS)
        self.assertEqual(to, "+256700900050")

    def test_prefers_email_when_set(self):
        self.tenant.preferred_notification = "EMAIL"
        self.tenant.save(update_fields=["preferred_notification"])
        channel, to = _resolve_tenant_channel(self.tenant)
        self.assertEqual(channel, Channel.EMAIL)
        self.assertEqual(to, "notify@example.com")

    def test_whatsapp_preference(self):
        self.tenant.preferred_notification = "WHATSAPP"
        self.tenant.save(update_fields=["preferred_notification"])
        channel, to = _resolve_tenant_channel(self.tenant)
        self.assertEqual(channel, Channel.WHATSAPP)


@override_settings(
    NOTIFICATION_PROVIDERS={"SMS": "console", "WHATSAPP": "console", "EMAIL": "console"},
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class EnqueueNotificationTests(NotifyFixture):
    def test_payment_confirmation_body_renders(self):
        delivery = enqueue_notification(
            template=Template.PAYMENT_CONFIRMATION,
            tenant=self.tenant,
            context={
                "tenant_name": self.tenant.full_name,
                "amount": 250000, "receipt_number": "RCP-1",
                "received_at": "2026-04-22 10:00",
            },
        )
        self.assertIn("250,000", delivery.body)
        self.assertIn("RCP-1", delivery.body)
        # Eager task should have run deliver_notification → SENT via console.
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, DeliveryStatus.SENT)

    def test_skipped_when_no_recipient(self):
        tenant = Tenant.objects.create(full_name="NoContact", phone="")
        delivery = enqueue_notification(
            template=Template.GENERIC, tenant=tenant,
            context={"message": "hello"},
        )
        self.assertEqual(delivery.status, DeliveryStatus.SKIPPED)


class ProviderSendTests(TestCase):
    def test_africas_talking_sms_parses_message_id(self):
        from notifications.providers import africas_talking as at_mod

        def handler(request):
            return httpx.Response(200, json={
                "SMSMessageData": {"Recipients": [{"messageId": "AT-123"}]}
            })
        transport = httpx.MockTransport(handler)
        real_client_cls = httpx.Client

        def mock_client(*a, **kw):
            kw["transport"] = transport
            return real_client_cls(*a, **kw)

        provider = at_mod.AfricasTalkingSMSProvider()
        delivery = NotificationDelivery.objects.create(
            recipient="+256700900050", channel=Channel.SMS,
            template=Template.GENERIC, body="Hi", context={},
        )
        with mock.patch.object(at_mod.httpx, "Client", side_effect=mock_client):
            result = provider.send(delivery)
        self.assertEqual(result["message_id"], "AT-123")

    def test_retry_policy_propagates_httpx_errors(self):
        """The task raises HTTPError so Celery can retry — the row stays FAILED
        in the DB between attempts."""
        from .tasks import deliver_notification

        delivery = NotificationDelivery.objects.create(
            recipient="+256700900050", channel=Channel.SMS,
            template=Template.GENERIC, body="Hi", context={},
        )

        class FakeProvider:
            name = "fake"

            def send(self, d):
                raise httpx.ConnectError("boom")

        with mock.patch("notifications.providers.get_provider", lambda c: FakeProvider()):
            with self.assertRaises(httpx.ConnectError):
                deliver_notification(delivery.pk)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, DeliveryStatus.FAILED)
        self.assertIn("boom", delivery.error_detail)


@override_settings(
    NOTIFICATION_PROVIDERS={"SMS": "console", "WHATSAPP": "console", "EMAIL": "console"},
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class BillingHookTests(NotifyFixture):
    def test_apply_payment_enqueues_confirmation(self):
        """Paying as a tenant triggers a PAYMENT_CONFIRMATION delivery row."""
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("50000"),
            method=Payment.Method.CASH, bank_account=self.bank,
            approval_status=ApprovalStatus.AUTO_APPROVED,
            received_at=timezone.now(),
        )
        apply_payment(payment)
        self.assertTrue(
            NotificationDelivery.objects.filter(
                tenant=self.tenant, template=Template.PAYMENT_CONFIRMATION,
            ).exists()
        )
