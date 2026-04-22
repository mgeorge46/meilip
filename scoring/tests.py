"""Tenant scoring tests — correctness of tier assignment, new-tenant
neutrality, weighted multi-house blending and on-time vs overdue effects."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    ApprovalStatus,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
)
from billing.sequences import allocate_number
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
from accounting.models import BankAccount
from accounting.utils import SYS_CASH, get_account

from .services import (
    NEUTRAL_SCORE_NEW_TENANT,
    calculate_score_for_tenant,
    calculate_scores_for_all,
)
from .tiers import Tier, tier_for_score


User = get_user_model()


def _make_user(email):
    return User.objects.create_user(
        email=email,
        phone=f"+25670{abs(hash(email)) % 100_000_00:08d}",
        password="pw-long-enough-1",
        first_name="T",
        last_name="S",
    )


class ScoringFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ugx = Currency.objects.get(code="UGX")
        cls.monthly = BillingCycle.objects.get(name="Monthly")
        cls.landlord = Landlord.objects.create(
            full_name="LL Ext", phone="+256700900001", is_meili_owned=False,
        )
        cls.landlord2 = Landlord.objects.create(
            full_name="LL Ext 2", phone="+256700900002", is_meili_owned=False,
        )
        cls.estate = Estate.objects.create(
            landlord=cls.landlord, name="E1",
            currency=cls.ugx, billing_cycle=cls.monthly,
            billing_mode=BillingMode.PREPAID,
        )
        cls.estate2 = Estate.objects.create(
            landlord=cls.landlord2, name="E2",
            currency=cls.ugx, billing_cycle=cls.monthly,
            billing_mode=BillingMode.PREPAID,
        )
        cls.house_small = House.objects.create(
            estate=cls.estate, house_number="S1", periodic_rent=Decimal("100000"),
        )
        cls.house_big = House.objects.create(
            estate=cls.estate2, house_number="B1", periodic_rent=Decimal("2000000"),
        )
        cls.tenant_user = _make_user("scoring-tenant@meili.test")
        cls.tenant = Tenant.objects.create(
            user=cls.tenant_user, full_name="Scoring Tenant",
            phone="+256700900099",
        )
        cls.cash = BankAccount.objects.create(
            name="Scoring Cash", kind=BankAccount.Kind.CASH,
            currency=cls.ugx, ledger_account=get_account(SYS_CASH),
        )

    def _issue_invoice(self, tenancy, y, m, amount, days_past_due=0):
        first = date(y, m, 1)
        last = (date(y, m + 1, 1) - timedelta(days=1)) if m < 12 else date(y, 12, 31)
        due = first + timedelta(days=5)
        status = Invoice.Status.ISSUED
        if days_past_due > 0:
            status = Invoice.Status.OVERDUE
        inv = Invoice.objects.create(
            tenant_house=tenancy, period_from=first, period_to=last,
            issue_date=first, due_date=due,
            rent_amount=amount, subtotal=amount, total=amount,
            status=status, number=allocate_number("INV"),
            issued_at=timezone.now(),
        )
        InvoiceLine.objects.create(
            invoice=inv, kind=InvoiceLine.Kind.RENT,
            description=f"Rent {y}-{m}", amount=amount,
            target=InvoiceLine.TARGET_LANDLORD,
        )
        return inv

    def _mark_paid(self, inv, pay_date):
        payment = Payment.objects.create(
            tenant=self.tenant, amount=inv.total,
            method=Payment.Method.CASH, bank_account=self.cash,
            approval_status=ApprovalStatus.AUTO_APPROVED,
            maker=self.tenant_user, received_at=timezone.now(),
        )
        # Force allocation row with the desired apply date.
        alloc = PaymentAllocation.objects.create(
            payment=payment, invoice=inv, amount=inv.total,
            is_advance_hold=False,
            applied_at=timezone.make_aware(
                timezone.datetime.combine(pay_date, timezone.datetime.min.time())
            ),
        )
        inv.transition_to(Invoice.Status.PAID)
        return alloc


class TierBoundaryTests(TestCase):
    def test_thresholds_inclusive(self):
        self.assertEqual(tier_for_score(100), Tier.PLATINUM)
        self.assertEqual(tier_for_score(90), Tier.PLATINUM)
        self.assertEqual(tier_for_score(89), Tier.GOLD)
        self.assertEqual(tier_for_score(75), Tier.GOLD)
        self.assertEqual(tier_for_score(74), Tier.SILVER)
        self.assertEqual(tier_for_score(60), Tier.SILVER)
        self.assertEqual(tier_for_score(59), Tier.BRONZE)
        self.assertEqual(tier_for_score(40), Tier.BRONZE)
        self.assertEqual(tier_for_score(39), Tier.WATCH)
        self.assertEqual(tier_for_score(0), Tier.WATCH)

    def test_invalid_inputs_map_to_watch(self):
        self.assertEqual(tier_for_score(None), Tier.WATCH)
        self.assertEqual(tier_for_score("not-a-number"), Tier.WATCH)


class NewTenantNeutralTests(ScoringFixture):
    def test_tenant_with_no_invoices_gets_neutral(self):
        TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_small,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
            move_in_date=date(2026, 4, 1),
        )
        score = calculate_score_for_tenant(self.tenant, today=date(2026, 4, 22))
        self.assertEqual(score.score, NEUTRAL_SCORE_NEW_TENANT)
        self.assertEqual(score.tier, Tier.SILVER)
        self.assertTrue(score.breakdown["new_tenant_neutral"])


class OnTimeVsLateTests(ScoringFixture):
    def test_always_on_time_scores_higher_than_always_late(self):
        t1 = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_small,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2025, 10, 1),
            move_in_date=date(2025, 10, 1),
        )
        # 6 invoices, all paid exactly on the due date.
        for m in range(10, 13):
            inv = self._issue_invoice(t1, 2025, m, Decimal("100000"))
            self._mark_paid(inv, inv.due_date)
        for m in range(1, 4):
            inv = self._issue_invoice(t1, 2026, m, Decimal("100000"))
            self._mark_paid(inv, inv.due_date)

        score_good = calculate_score_for_tenant(self.tenant, today=date(2026, 4, 22))
        good = score_good.score

        # Reset: create a DIFFERENT tenant who pays 20 days late every time.
        bad_user = _make_user("bad-tenant@meili.test")
        bad_tenant = Tenant.objects.create(
            user=bad_user, full_name="Bad Payer", phone="+256700900100",
        )
        t_bad = TenantHouse.objects.create(
            tenant=bad_tenant, house=House.objects.create(
                estate=self.estate, house_number="S2", periodic_rent=Decimal("100000"),
            ),
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2025, 10, 1),
            move_in_date=date(2025, 10, 1),
        )
        for m in range(10, 13):
            first = date(2025, m, 1)
            last = (date(2025, m + 1, 1) - timedelta(days=1)) if m < 12 else date(2025, 12, 31)
            due = first + timedelta(days=5)
            inv = Invoice.objects.create(
                tenant_house=t_bad, period_from=first, period_to=last,
                issue_date=first, due_date=due,
                rent_amount=Decimal("100000"), subtotal=Decimal("100000"),
                total=Decimal("100000"),
                status=Invoice.Status.ISSUED, number=allocate_number("INV"),
                issued_at=timezone.now(),
            )
            # "Pay" 20 days late.
            pay = Payment.objects.create(
                tenant=bad_tenant, amount=inv.total,
                method=Payment.Method.CASH, bank_account=self.cash,
                approval_status=ApprovalStatus.AUTO_APPROVED,
                maker=bad_user,
            )
            PaymentAllocation.objects.create(
                payment=pay, invoice=inv, amount=inv.total,
                is_advance_hold=False,
                applied_at=timezone.make_aware(
                    timezone.datetime.combine(
                        due + timedelta(days=20), timezone.datetime.min.time()
                    )
                ),
            )
            inv.transition_to(Invoice.Status.PAID)

        score_bad = calculate_score_for_tenant(bad_tenant, today=date(2026, 4, 22))
        self.assertGreater(good, score_bad.score)
        self.assertGreaterEqual(good, 75)  # Gold or better
        self.assertLess(score_bad.score, good)


class MultiHouseWeightingTests(ScoringFixture):
    def test_big_house_dominates_blended_score(self):
        # Small house: perfect record.
        t_small = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_small,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 1, 1),
            move_in_date=date(2026, 1, 1),
        )
        for m in [1, 2, 3]:
            inv = self._issue_invoice(t_small, 2026, m, Decimal("100000"))
            self._mark_paid(inv, inv.due_date)

        # Big house: huge outstanding, overdue.
        t_big = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_big,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 1, 1),
            move_in_date=date(2026, 1, 1),
        )
        for m in [1, 2, 3]:
            self._issue_invoice(
                t_big, 2026, m, Decimal("2000000"), days_past_due=40
            )

        score = calculate_score_for_tenant(self.tenant, today=date(2026, 4, 22))
        # Big house dominates by total_billed (6M vs 300k) -> score should be
        # pulled DOWN by the 2M outstanding overdue house.
        self.assertLess(score.score, 60)


class BulkCalculationTests(ScoringFixture):
    def test_calculate_scores_for_all_processes_roster(self):
        TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_small,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
            move_in_date=date(2026, 4, 1),
        )
        # Additional tenant.
        u2 = _make_user("bulk2@meili.test")
        Tenant.objects.create(
            user=u2, full_name="Bulk Two", phone="+256700900200",
        )
        result = calculate_scores_for_all(today=date(2026, 4, 22))
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["errors"], [])
