"""Inbound webhook tests — auth, idempotency, matching, rate-limit, and
full happy-path integration with the billing ledger."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounting.models import BankAccount
from accounting.utils import SYS_CASH, get_account
from api.models import ApiKey, WebhookEvent
from billing.models import Invoice, InvoiceLine, Payment
from billing.sequences import allocate_number
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

User = get_user_model()

WEBHOOK_URL = "/api/v1/payments/"


def _make_user(email):
    return User.objects.create_user(
        email=email,
        phone=f"+25670{abs(hash(email)) % 100_000_00:08d}",
        password="pw-long-enough-1",
        first_name="A", last_name="B",
    )


class ApiFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ugx = Currency.objects.get(code="UGX")
        cls.cycle = BillingCycle.objects.get(name="Monthly")
        cls.landlord = Landlord.objects.create(
            full_name="LL", phone="+256700000001", is_meili_owned=False,
        )
        cls.estate = Estate.objects.create(
            landlord=cls.landlord, name="E", currency=cls.ugx,
            billing_cycle=cls.cycle, billing_mode=BillingMode.PREPAID,
        )
        cls.house = House.objects.create(
            estate=cls.estate, house_number="H1", periodic_rent=Decimal("300000"),
        )
        cls.tenant = Tenant.objects.create(
            full_name="Phone Tenant", phone="+256700900123",
        )
        cls.tenancy = TenantHouse.objects.create(
            tenant=cls.tenant, house=cls.house,
            status=TenantHouse.Status.ACTIVE,
            move_in_date=timezone.localdate(),
            billing_start_date=timezone.localdate(),
        )
        cls.bank = BankAccount.objects.create(
            name="MoMo", kind=BankAccount.Kind.MOBILE_MONEY,
            currency=cls.ugx, ledger_account=get_account(SYS_CASH),
        )
        cls.api_key, cls.raw_key = ApiKey.issue(
            name="Test Bank", bank_account=cls.bank,
        )

    def _post(self, payload, *, key=None):
        return self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_X_API_KEY=(key if key is not None else self.raw_key),
        )


class ApiKeyIssueTests(TestCase):
    def test_issue_stores_hash_not_raw(self):
        ugx = Currency.objects.get(code="UGX")
        ba = BankAccount.objects.create(
            name="Bank", kind=BankAccount.Kind.BANK,
            currency=ugx, ledger_account=get_account(SYS_CASH),
        )
        key, raw = ApiKey.issue(name="Test", bank_account=ba)
        self.assertTrue(raw.startswith("mk_"))
        self.assertEqual(key.key_prefix, raw[:12])
        self.assertNotIn(raw, key.hashed_key)
        self.assertTrue(key.verify(raw))
        self.assertFalse(key.verify(raw + "x"))


class WebhookAuthTests(ApiFixture):
    def test_missing_header_returns_401(self):
        resp = self.client.post(WEBHOOK_URL, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    def test_bad_key_returns_401(self):
        resp = self._post(
            {"amount": 1, "payer_reference": "x", "transaction_id": "t",
             "timestamp": "2026-04-22T10:00:00Z"},
            key="mk_invalidkeyhere_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        )
        self.assertEqual(resp.status_code, 401)

    def test_revoked_key_returns_401(self):
        self.api_key.revoke()
        resp = self._post(
            {"amount": 1, "payer_reference": "x", "transaction_id": "t",
             "timestamp": "2026-04-22T10:00:00Z"},
        )
        self.assertEqual(resp.status_code, 401)


@override_settings(RATELIMIT_ENABLE=False)
class WebhookValidationTests(ApiFixture):
    def test_malformed_payload_returns_400_and_logs_event(self):
        resp = self._post({"amount": "not-a-number", "transaction_id": "bad-1"})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(
            WebhookEvent.objects.filter(
                transaction_id="bad-1", status=WebhookEvent.Status.INVALID,
            ).exists()
        )


@override_settings(RATELIMIT_ENABLE=False)
class WebhookHappyPathTests(ApiFixture):
    def test_accepted_creates_payment_and_receipt(self):
        # Issue a 300k invoice so FIFO has something to land on.
        inv = Invoice.objects.create(
            tenant_house=self.tenancy,
            period_from=timezone.localdate(),
            period_to=timezone.localdate(),
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            rent_amount=Decimal("300000"),
            subtotal=Decimal("300000"), total=Decimal("300000"),
            status=Invoice.Status.ISSUED, number=allocate_number("INV"),
            issued_at=timezone.now(),
        )
        InvoiceLine.objects.create(
            invoice=inv, kind=InvoiceLine.Kind.RENT,
            description="Rent", amount=Decimal("300000"),
            target=InvoiceLine.TARGET_LANDLORD,
        )
        resp = self._post({
            "amount": 300000, "payer_reference": "+256700900123",
            "transaction_id": "TX-001",
            "timestamp": "2026-04-22T10:00:00Z",
            "source_name": "Stanbic MoMo",
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "accepted")
        self.assertTrue(body["receipt_number"])
        payment = Payment.objects.get(pk=body["payment_id"])
        self.assertEqual(payment.tenant_id, self.tenant.pk)
        self.assertEqual(payment.amount, Decimal("300000"))
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.PAID)

    def test_matches_by_phone_tail(self):
        """Gateway sends `0700900123` — should resolve to +256700900123."""
        resp = self._post({
            "amount": 100000, "payer_reference": "0700900123",
            "transaction_id": "TX-002",
            "timestamp": "2026-04-22T10:00:00Z",
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["status"], "accepted")

    def test_unmatched_payer_returns_202(self):
        resp = self._post({
            "amount": 1000, "payer_reference": "+256700999999",  # no such tenant
            "transaction_id": "TX-missing",
            "timestamp": "2026-04-22T10:00:00Z",
        })
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "unmatched")
        self.assertFalse(Payment.objects.filter(reference_number="TX-missing").exists())

    def test_duplicate_transaction_id_is_idempotent(self):
        payload = {
            "amount": 50000, "payer_reference": "+256700900123",
            "transaction_id": "TX-DUP",
            "timestamp": "2026-04-22T10:00:00Z",
        }
        first = self._post(payload)
        self.assertEqual(first.status_code, 201)
        second = self._post(payload)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["status"], "duplicate")
        # Only one Payment row
        self.assertEqual(
            Payment.objects.filter(reference_number="TX-DUP").count(), 1
        )


@override_settings(RATELIMIT_ENABLE=False)
class WebhookIPAllowlistTests(ApiFixture):
    def test_blocked_when_ip_not_allowed(self):
        self.api_key.allowed_ips = "10.0.0.1, 10.0.0.2"
        self.api_key.save(update_fields=["allowed_ips"])
        resp = self.client.post(
            WEBHOOK_URL,
            data={
                "amount": 1, "payer_reference": "x",
                "transaction_id": "t1", "timestamp": "2026-04-22T10:00:00Z",
            },
            content_type="application/json",
            HTTP_X_API_KEY=self.raw_key,
            REMOTE_ADDR="8.8.8.8",
        )
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# Outbound notifications API — POST /api/v1/notifications/
# ---------------------------------------------------------------------------
NOTIFY_URL = "/api/v1/notifications/"


@override_settings(
    RATELIMIT_ENABLE=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    NOTIFICATION_PROVIDERS={"SMS": "console", "WHATSAPP": "console", "EMAIL": "console"},
)
class NotificationSendTests(ApiFixture):
    def _post(self, payload, *, key=None):
        return self.client.post(
            NOTIFY_URL, data=payload, content_type="application/json",
            HTTP_X_API_KEY=(key if key is not None else self.raw_key),
        )

    def test_auth_required(self):
        resp = self.client.post(NOTIFY_URL, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    def test_queues_delivery_for_tenant(self):
        from notifications.models import NotificationDelivery
        resp = self._post({
            "template": "GENERIC",
            "tenant_id": self.tenant.pk,
            "context": {"message": "Hello tenant"},
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "queued")
        self.assertTrue(NotificationDelivery.objects.filter(pk=body["delivery_id"]).exists())

    def test_missing_tenant_returns_404(self):
        resp = self._post({
            "template": "GENERIC",
            "tenant_id": 999999,
            "context": {"message": "x"},
        })
        self.assertEqual(resp.status_code, 404)

    def test_idempotent_replay(self):
        payload = {
            "template": "GENERIC",
            "tenant_id": self.tenant.pk,
            "context": {"message": "Same-as-before"},
            "idempotency_key": "NOTIF-DUP-1",
        }
        first = self._post(payload)
        self.assertEqual(first.status_code, 201, first.content)
        second = self._post(payload)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["status"], "duplicate")

    def test_generic_requires_message(self):
        resp = self._post({
            "template": "GENERIC",
            "tenant_id": self.tenant.pk,
            "context": {},
        })
        self.assertEqual(resp.status_code, 400)

    def test_raw_recipient_when_no_party(self):
        resp = self._post({
            "template": "GENERIC",
            "recipient": "+256700999111",
            "channel": "SMS",
            "context": {"message": "hi"},
        })
        self.assertEqual(resp.status_code, 201, resp.content)


@override_settings(RATELIMIT_ENABLE=False)
class NotificationStatusViewTests(ApiFixture):
    def test_returns_delivery_row(self):
        from notifications.models import Channel, DeliveryStatus, NotificationDelivery
        delivery = NotificationDelivery.objects.create(
            template="GENERIC", channel=Channel.SMS,
            recipient="+256700111222", status=DeliveryStatus.QUEUED,
            context={"message": "x"},
        )
        resp = self.client.get(
            f"{NOTIFY_URL}{delivery.pk}/",
            HTTP_X_API_KEY=self.raw_key,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["id"], delivery.pk)
        self.assertEqual(body["status"], DeliveryStatus.QUEUED)

    def test_missing_returns_404(self):
        resp = self.client.get(f"{NOTIFY_URL}999999/", HTTP_X_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# End-to-end integration — Phase 8
# Tenancy → Invoice → Webhook payment → FIFO allocation → Receipt →
# Commission posting → Notification enqueued.
# ---------------------------------------------------------------------------
@override_settings(
    RATELIMIT_ENABLE=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    NOTIFICATION_PROVIDERS={"SMS": "console", "WHATSAPP": "console", "EMAIL": "console"},
    AXES_ENABLED=False,
)
class EndToEndPipelineTests(TestCase):
    """Exercises the whole rent-collection pipeline through public seams."""

    def test_webhook_payment_posts_commission_and_queues_notification(self):
        from accounting.models import BankAccount
        from accounting.utils import (
            SYS_CASH, SYS_COMMISSION_INCOME, SYS_LANDLORD_PAYABLE, get_account,
        )
        from billing.models import (
            CommissionPosting, Invoice, InvoiceLine, Payment,
        )
        from billing.sequences import allocate_number
        from core.models import (
            BillingCycle, BillingMode, CommissionScope, CommissionType,
            Currency, Estate, House, Landlord, Tenant, TenantHouse,
        )
        from notifications.models import NotificationDelivery

        ugx = Currency.objects.get(code="UGX")
        cycle = BillingCycle.objects.get(name="Monthly")

        # Landlord + managed estate with 10% commission
        landlord = Landlord.objects.create(
            full_name="Managed LL", phone="+256700888001", is_meili_owned=False,
        )
        estate = Estate.objects.create(
            landlord=landlord, name="Mgd Estate", currency=ugx,
            billing_cycle=cycle, billing_mode=BillingMode.PREPAID,
            commission_type=CommissionType.PERCENTAGE,
            commission_scope=CommissionScope.PER_ESTATE,
            commission_percent=Decimal("10.000"),
        )
        house = House.objects.create(
            estate=estate, house_number="E2E-01", periodic_rent=Decimal("500000"),
        )
        tenant = Tenant.objects.create(
            full_name="E2E Tenant", phone="+256700777001",
        )
        tenancy = TenantHouse.objects.create(
            tenant=tenant, house=house,
            status=TenantHouse.Status.ACTIVE,
            move_in_date=timezone.localdate(),
            billing_start_date=timezone.localdate(),
        )

        # Bank account + API key
        bank = BankAccount.objects.create(
            name="E2E MoMo", kind=BankAccount.Kind.MOBILE_MONEY,
            currency=ugx, ledger_account=get_account(SYS_CASH),
        )
        api_key, raw_key = ApiKey.issue(name="E2E Bank", bank_account=bank)

        # Seed an issued invoice for the current period
        inv = Invoice.objects.create(
            tenant_house=tenancy,
            period_from=timezone.localdate(),
            period_to=timezone.localdate(),
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            rent_amount=Decimal("500000"),
            subtotal=Decimal("500000"), total=Decimal("500000"),
            status=Invoice.Status.ISSUED, number=allocate_number("INV"),
            issued_at=timezone.now(),
        )
        InvoiceLine.objects.create(
            invoice=inv, kind=InvoiceLine.Kind.RENT,
            description="Monthly rent", amount=Decimal("500000"),
            target=InvoiceLine.TARGET_LANDLORD,
        )

        # Webhook: full payment comes in
        resp = self.client.post(
            WEBHOOK_URL,
            data={
                "amount": 500000,
                "payer_reference": "+256700777001",
                "transaction_id": "E2E-TX-01",
                "timestamp": "2026-04-22T10:30:00Z",
                "source_name": "MoMo",
            },
            content_type="application/json",
            HTTP_X_API_KEY=raw_key,
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "accepted")
        self.assertTrue(body["receipt_number"])

        # Payment + receipt recorded
        payment = Payment.objects.get(pk=body["payment_id"])
        self.assertEqual(payment.amount, Decimal("500000"))
        self.assertTrue(payment.receipts.exists())

        # Invoice is fully paid via FIFO allocation
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.PAID)

        # Commission posting — 10% of 500,000 = 50,000
        cp = CommissionPosting.objects.get(invoice=inv, is_reversal=False)
        self.assertEqual(cp.amount, Decimal("50000"))

        # Journal entry debits Landlord Payable and credits Commission Income
        lines = list(cp.journal_entry.lines.all())
        debited = {l.account_id: l.debit for l in lines if l.debit > 0}
        credited = {l.account_id: l.credit for l in lines if l.credit > 0}
        self.assertIn(get_account(SYS_LANDLORD_PAYABLE).pk, debited)
        self.assertIn(get_account(SYS_COMMISSION_INCOME).pk, credited)
        self.assertEqual(debited[get_account(SYS_LANDLORD_PAYABLE).pk], Decimal("50000"))
        self.assertEqual(credited[get_account(SYS_COMMISSION_INCOME).pk], Decimal("50000"))

        # Notification was queued (CELERY eager -> SENT via console provider)
        delivery = NotificationDelivery.objects.filter(
            template="PAYMENT_CONFIRMATION",
        ).first()
        self.assertIsNotNone(delivery, "payment confirmation notification not queued")
