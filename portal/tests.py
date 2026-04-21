"""Portal tests — cross-tenant / cross-landlord data isolation,
statement window enforcement, and held-advance exclusion (SPEC §20)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounting.utils import (
    SYS_ALLOWANCES_EXPENSE,
    SYS_NSSF_EMPLOYER_EXPENSE,
    SYS_NSSF_PAYABLE,
    SYS_PAYE_PAYABLE,
    SYS_SALARIES_EXPENSE,
    SYS_SALARIES_PAYABLE,
    SYS_STAFF_ADVANCES_RECEIVABLE,
    get_account,
)
from billing.models import Invoice
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

from .services import (
    MAX_STATEMENT_MONTHS,
    StatementWindowError,
    build_statement_context,
    enforce_window,
)


User = get_user_model()


def make_user(email, phone):
    return User.objects.create_user(
        email=email, phone=phone, password="pw-long-enough-1",
        first_name="F", last_name="L",
    )


class PortalFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ugx = Currency.objects.get(code="UGX")
        cls.monthly = BillingCycle.objects.get(name="Monthly")
        cls.landlord_a_user = make_user("la@meili.test", "+256700500001")
        cls.landlord_b_user = make_user("lb@meili.test", "+256700500002")
        cls.landlord_a = Landlord.objects.create(
            user=cls.landlord_a_user, full_name="Landlord A",
            phone="+256700500001", email="la@meili.test",
        )
        cls.landlord_b = Landlord.objects.create(
            user=cls.landlord_b_user, full_name="Landlord B",
            phone="+256700500002", email="lb@meili.test",
        )
        cls.estate_a = Estate.objects.create(
            landlord=cls.landlord_a, name="Estate A",
            currency=cls.ugx, billing_cycle=cls.monthly,
            billing_mode=BillingMode.PREPAID,
        )
        cls.estate_b = Estate.objects.create(
            landlord=cls.landlord_b, name="Estate B",
            currency=cls.ugx, billing_cycle=cls.monthly,
            billing_mode=BillingMode.PREPAID,
        )
        cls.house_a = House.objects.create(
            estate=cls.estate_a, house_number="A1",
            periodic_rent=Decimal("1000000"),
        )
        cls.house_b = House.objects.create(
            estate=cls.estate_b, house_number="B1",
            periodic_rent=Decimal("900000"),
        )

        cls.tenant_a_user = make_user("ta@meili.test", "+256700600001")
        cls.tenant_b_user = make_user("tb@meili.test", "+256700600002")
        cls.tenant_a = Tenant.objects.create(
            user=cls.tenant_a_user, full_name="Tenant A", phone="+256700600001",
        )
        cls.tenant_b = Tenant.objects.create(
            user=cls.tenant_b_user, full_name="Tenant B", phone="+256700600002",
        )
        cls.th_a = TenantHouse.objects.create(
            tenant=cls.tenant_a, house=cls.house_a,
            status=TenantHouse.Status.ACTIVE,
            move_in_date=date(2026, 1, 1),
        )
        cls.th_b = TenantHouse.objects.create(
            tenant=cls.tenant_b, house=cls.house_b,
            status=TenantHouse.Status.ACTIVE,
            move_in_date=date(2026, 1, 1),
        )
        cls.inv_a = Invoice.objects.create(
            tenant_house=cls.th_a,
            period_from=date(2026, 2, 1), period_to=date(2026, 2, 28),
            issue_date=date(2026, 2, 1), due_date=date(2026, 2, 10),
            rent_amount=Decimal("1000000"),
            subtotal=Decimal("1000000"),
            total=Decimal("1000000"),
            status=Invoice.Status.ISSUED, number="INV-A",
        )
        cls.inv_b = Invoice.objects.create(
            tenant_house=cls.th_b,
            period_from=date(2026, 2, 1), period_to=date(2026, 2, 28),
            issue_date=date(2026, 2, 1), due_date=date(2026, 2, 10),
            rent_amount=Decimal("900000"),
            subtotal=Decimal("900000"),
            total=Decimal("900000"),
            status=Invoice.Status.ISSUED, number="INV-B",
        )

    def _login(self, user):
        self.client.force_login(user)


class TenantPortalIsolationTests(PortalFixture):
    def test_tenant_sees_only_own_invoices(self):
        self._login(self.tenant_a_user)
        r = self.client.get(reverse("tenant:invoice-list"))
        self.assertEqual(r.status_code, 200)
        invs = list(r.context["invoices"])
        self.assertEqual([i.pk for i in invs], [self.inv_a.pk])

    def test_tenant_cannot_open_other_tenants_invoice(self):
        self._login(self.tenant_a_user)
        r = self.client.get(reverse("tenant:invoice-detail", args=[self.inv_b.pk]))
        self.assertEqual(r.status_code, 404)

    def test_non_tenant_blocked(self):
        stranger = make_user("nobody@meili.test", "+256700777777")
        self._login(stranger)
        r = self.client.get(reverse("tenant:dashboard"))
        self.assertEqual(r.status_code, 403)


class LandlordPortalIsolationTests(PortalFixture):
    def test_landlord_sees_only_own_houses(self):
        self._login(self.landlord_a_user)
        r = self.client.get(reverse("landlord:house-list"))
        self.assertEqual(r.status_code, 200)
        houses = list(r.context["houses"])
        self.assertEqual([h.pk for h in houses], [self.house_a.pk])

    def test_non_landlord_blocked(self):
        self._login(self.tenant_a_user)
        r = self.client.get(reverse("landlord:dashboard"))
        self.assertEqual(r.status_code, 403)


class StatementWindowTests(TestCase):
    def test_six_month_window_allowed(self):
        enforce_window(date(2026, 1, 1), date(2026, 6, 30))

    def test_seven_month_window_rejected(self):
        with self.assertRaises(StatementWindowError):
            enforce_window(date(2026, 1, 1), date(2026, 7, 31))

    def test_reversed_window_rejected(self):
        with self.assertRaises(StatementWindowError):
            enforce_window(date(2026, 6, 1), date(2026, 1, 1))

    def test_max_months_constant(self):
        self.assertEqual(MAX_STATEMENT_MONTHS, 6)


class StatementExcludesHeldAdvanceTests(PortalFixture):
    def test_context_uses_invoice_totals_not_held_advance(self):
        ctx = build_statement_context(
            self.landlord_a, date(2026, 2, 1), date(2026, 2, 28)
        )
        self.assertEqual(len(ctx.rows), 1)
        self.assertEqual(ctx.rows[0].cost, 1_000_000)

    def test_other_landlords_invoices_excluded(self):
        ctx = build_statement_context(
            self.landlord_a, date(2026, 2, 1), date(2026, 2, 28)
        )
        tenants_on_statement = {r.tenant for r in ctx.rows}
        self.assertNotIn(self.tenant_b.full_name, tenants_on_statement)


class PayrollChartOfAccountsTests(TestCase):
    def test_all_payroll_accounts_seeded(self):
        for code in (
            SYS_STAFF_ADVANCES_RECEIVABLE,
            SYS_SALARIES_PAYABLE,
            SYS_PAYE_PAYABLE,
            SYS_NSSF_PAYABLE,
            SYS_SALARIES_EXPENSE,
            SYS_ALLOWANCES_EXPENSE,
            SYS_NSSF_EMPLOYER_EXPENSE,
        ):
            acct = get_account(code)
            self.assertTrue(acct.is_active, f"{code} should be active")
            self.assertTrue(acct.is_postable, f"{code} should be postable")
