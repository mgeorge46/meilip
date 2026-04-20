"""Billing engine tests — focus on correctness of money flows and guards,
NOT trivial CRUD. See CLAUDE.md §Credit-Efficiency rules."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounting.models import BankAccount, JournalEntry
from accounting.utils import (
    SYS_AR_TENANTS,
    SYS_CASH,
    SYS_COMMISSION_INCOME,
    SYS_LANDLORD_PAYABLE,
    SYS_RENT_INCOME,
    SYS_TENANT_ADVANCE_HELD_MANAGED,
    SYS_TENANT_ADVANCE_HELD_MEILI,
    get_account,
)
from core.models import (
    BillingCycle,
    BillingMode,
    Currency,
    Employee,
    Estate,
    House,
    Landlord,
    Tenant,
    TenantHouse,
)

from .exceptions import (
    CreditNoteExceedsInvoice,
    InvalidInvoiceTransition,
    ProtectedFinancialRecord,
    SelfApprovalBlocked,
)
from .models import (
    AdHocCharge,
    ApprovalStatus,
    CreditNote,
    Invoice,
    InvoiceLine,
    InvoiceVoid,
    Payment,
    PaymentAllocation,
    Refund,
)
from .sequences import NumberSequence, allocate_number
from .services import (
    apply_payment,
    execute_credit_note,
    execute_refund,
    execute_void,
    generate_invoice_for_tenancy,
    mark_overdue_invoices,
)


User = get_user_model()


def make_user(email):
    return User.objects.create_user(
        email=email,
        phone=f"+25670{abs(hash(email)) % 100_000_00:08d}",
        password="pw-long-enough-1",
        first_name="T",
        last_name="User",
    )


class BillingFixture(TestCase):
    """Shared tenancy/house/account setup for billing tests."""

    @classmethod
    def setUpTestData(cls):
        cls.ugx = Currency.objects.get(code="UGX")
        cls.monthly = BillingCycle.objects.get(name="Monthly")
        cls.landlord_ext = Landlord.objects.create(
            full_name="Ext Landlord", phone="+256700300001", is_meili_owned=False,
        )
        cls.landlord_meili = Landlord.objects.create(
            full_name="Meili Co", phone="+256700300002", is_meili_owned=True,
        )
        cls.estate_ext = Estate.objects.create(
            landlord=cls.landlord_ext, name="Managed Estate",
            currency=cls.ugx, billing_cycle=cls.monthly,
            billing_mode=BillingMode.PREPAID,
            commission_type="PERCENTAGE",
            commission_percent=Decimal("10.000"),
        )
        cls.estate_meili = Estate.objects.create(
            landlord=cls.landlord_meili, name="Meili Estate",
            currency=cls.ugx, billing_cycle=cls.monthly,
            billing_mode=BillingMode.PREPAID,
        )
        cls.house_ext = House.objects.create(
            estate=cls.estate_ext, house_number="A1",
            periodic_rent=Decimal("1000000"),
        )
        cls.house_meili = House.objects.create(
            estate=cls.estate_meili, house_number="M1",
            periodic_rent=Decimal("800000"),
        )
        cls.tenant_user = make_user("tenant-billing@meili.test")
        cls.tenant = Tenant.objects.create(
            user=cls.tenant_user, full_name="Tenant One", phone="+256700400001",
        )
        cls.tenancy_ext = TenantHouse.objects.create(
            tenant=cls.tenant, house=cls.house_ext,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
            move_in_date=date(2026, 4, 1),
        )
        cls.cash_account = get_account(SYS_CASH)
        cls.bank_account = BankAccount.objects.create(
            name="Cash Drawer", kind=BankAccount.Kind.CASH,
            currency=cls.ugx, ledger_account=cls.cash_account,
        )
        # Two staff users for maker/checker
        cls.maker_user = make_user("maker@meili.test")
        cls.checker_user = make_user("checker@meili.test")
        cls.emp_maker = Employee.objects.create(
            user=cls.maker_user, full_name="Maker", requires_checker=True,
        )
        cls.emp_checker = Employee.objects.create(
            user=cls.checker_user, full_name="Checker", requires_checker=False,
        )
        cls.trusted_user = make_user("trusted@meili.test")
        cls.emp_trusted = Employee.objects.create(
            user=cls.trusted_user, full_name="Trusty", requires_checker=False,
        )


class SequentialNumberingTests(BillingFixture):
    def test_allocate_is_atomic_and_unique(self):
        seen = set()
        for _ in range(5):
            n = allocate_number("INV")
            self.assertNotIn(n, seen)
            seen.add(n)
        self.assertTrue(all(n.startswith("INV-") for n in seen))

    def test_voided_invoice_keeps_number_and_creates_gap_signal(self):
        inv = self._issue_invoice()
        first_no = inv.number
        self.assertIsNotNone(first_no)
        # Raise a void so the number is kept; generate next number
        self._void_invoice(inv)
        inv.refresh_from_db()
        self.assertEqual(inv.number, first_no)
        self.assertEqual(inv.status, Invoice.Status.VOIDED)

    def _issue_invoice(self):
        draft = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user)
        return draft.invoice

    def _void_invoice(self, inv):
        void = InvoiceVoid.objects.create(
            invoice=inv, reason_category=InvoiceVoid.Reason.DATA_ENTRY_ERROR,
            reason="duplicate", maker=self.maker_user, submitted_at=timezone.now(),
        )
        void.approve(self.checker_user)
        execute_void(void, user=self.checker_user)


class StateMachineGuardTests(BillingFixture):
    def test_only_draft_or_cancelled_can_be_deleted(self):
        draft = Invoice.objects.create(
            tenant_house=self.tenancy_ext, period_from=date(2026, 4, 1),
            period_to=date(2026, 4, 30), due_date=date(2026, 4, 14),
            rent_amount=Decimal("1000000"),
        )
        # Draft delete — OK
        draft_copy = Invoice.objects.create(
            tenant_house=self.tenancy_ext, period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31), due_date=date(2026, 5, 14),
            rent_amount=Decimal("1000000"),
        )
        draft_copy.delete()
        self.assertFalse(Invoice.objects.filter(pk=draft_copy.pk).exists())
        # Issued delete — blocked
        draft.status = Invoice.Status.ISSUED
        draft.number = "INV-TEST"
        draft.save()
        with self.assertRaises(ProtectedFinancialRecord):
            draft.delete()

    def test_illegal_transitions_raise(self):
        inv = Invoice.objects.create(
            tenant_house=self.tenancy_ext, period_from=date(2026, 4, 1),
            period_to=date(2026, 4, 30), due_date=date(2026, 4, 14),
            rent_amount=Decimal("1000000"),
        )
        with self.assertRaises(InvalidInvoiceTransition):
            inv.transition_to(Invoice.Status.PAID)


class InvoiceGenerationTests(BillingFixture):
    def test_generates_issued_invoice_with_journal(self):
        draft = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user)
        inv = draft.invoice
        self.assertEqual(inv.status, Invoice.Status.ISSUED)
        self.assertEqual(inv.total, Decimal("1000000"))
        self.assertTrue(inv.source_journal.is_balanced())
        # Managed landlord — credit goes to landlord payable, not rent income
        landlord_payable = get_account(SYS_LANDLORD_PAYABLE)
        self.assertEqual(landlord_payable.balance(), Decimal("1000000"))
        ar = get_account(SYS_AR_TENANTS)
        self.assertEqual(ar.balance(), Decimal("1000000"))
        # Commission not yet recognised (only at allocation)
        self.assertEqual(get_account(SYS_COMMISSION_INCOME).balance(), Decimal("0"))

    def test_generation_blocked_when_paused(self):
        self.tenancy_ext.invoice_generation_status = TenantHouse.InvoiceGenerationStatus.PAUSED
        self.tenancy_ext.save()
        from .exceptions import InvoiceGenerationPaused
        with self.assertRaises(InvoiceGenerationPaused):
            generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user)


class FIFOAllocationTests(BillingFixture):
    def _issue_invoices_for_months(self, months):
        invoices = []
        base_start = date(2026, 4, 1)
        for i, m in enumerate(months):
            first = date(2026, m, 1)
            last = (date(2026, m + 1, 1) - timedelta(days=1)) if m < 12 else date(2026, 12, 31)
            inv = Invoice.objects.create(
                tenant_house=self.tenancy_ext, period_from=first, period_to=last,
                due_date=first + timedelta(days=14),
                rent_amount=Decimal("1000000"), subtotal=Decimal("1000000"),
                total=Decimal("1000000"),
                status=Invoice.Status.ISSUED, number=allocate_number("INV"),
                issued_at=timezone.now(),
            )
            InvoiceLine.objects.create(
                invoice=inv, kind=InvoiceLine.Kind.RENT,
                description=f"Rent m{m}", amount=Decimal("1000000"),
                target=InvoiceLine.TARGET_LANDLORD,
            )
            invoices.append(inv)
        return invoices

    def test_fifo_applies_to_oldest_first_with_surplus_to_advance(self):
        inv_a, inv_b = self._issue_invoices_for_months([4, 5])
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("1500000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.emp_trusted.user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.emp_trusted.user)
        inv_a.refresh_from_db(); inv_b.refresh_from_db()
        self.assertEqual(inv_a.status, Invoice.Status.PAID)
        self.assertEqual(inv_a.outstanding, Decimal("0"))
        self.assertEqual(inv_b.status, Invoice.Status.PARTIALLY_PAID)
        self.assertEqual(inv_b.amount_paid, Decimal("500000"))
        self.assertEqual(inv_b.outstanding, Decimal("500000"))
        # No surplus → no advance hold
        self.assertFalse(
            PaymentAllocation.objects.filter(payment=payment, is_advance_hold=True).exists()
        )

    def test_surplus_routes_to_managed_advance_account(self):
        inv_a, = self._issue_invoices_for_months([4])
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("1500000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.emp_trusted.user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.emp_trusted.user)
        managed = get_account(SYS_TENANT_ADVANCE_HELD_MANAGED)
        meili = get_account(SYS_TENANT_ADVANCE_HELD_MEILI)
        self.assertEqual(managed.balance(), Decimal("500000"))
        self.assertEqual(meili.balance(), Decimal("0"))


class CommissionTests(BillingFixture):
    def test_commission_recognised_on_allocation_for_managed(self):
        inv, = self._issue_and_return([4])
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("1000000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.emp_trusted.user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.emp_trusted.user)
        # 10% of 1,000,000
        self.assertEqual(get_account(SYS_COMMISSION_INCOME).balance(), Decimal("100000"))
        # Landlord payable: 1,000,000 - 100,000 = 900,000
        self.assertEqual(get_account(SYS_LANDLORD_PAYABLE).balance(), Decimal("900000"))

    def test_no_commission_for_meili_owned(self):
        tenancy_m = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_meili,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
        )
        draft = generate_invoice_for_tenancy(tenancy_m, user=self.maker_user)
        inv = draft.invoice
        payment = Payment.objects.create(
            tenant=self.tenant, amount=inv.total,
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.emp_trusted.user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.emp_trusted.user)
        self.assertEqual(get_account(SYS_COMMISSION_INCOME).balance(), Decimal("0"))
        self.assertEqual(get_account(SYS_RENT_INCOME).balance(), Decimal("800000"))

    def _issue_and_return(self, months):
        out = []
        for m in months:
            self.tenancy_ext.billing_start_date = date(2026, m, 1)
            self.tenancy_ext.save()
            draft = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user)
            if draft:
                out.append(draft.invoice)
        return out


class MakerCheckerTests(BillingFixture):
    def test_self_approval_blocked(self):
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("100000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.maker_user, submitted_at=timezone.now(),
        )
        with self.assertRaises(SelfApprovalBlocked):
            payment.approve(self.maker_user)

    def test_trusted_bypass_for_payment(self):
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("100000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user, submitted_at=timezone.now(),
        )
        self.assertTrue(payment.try_trusted_autoapprove())
        self.assertEqual(payment.approval_status, ApprovalStatus.AUTO_APPROVED)

    def test_void_never_allows_trusted_bypass(self):
        inv = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user).invoice
        void = InvoiceVoid.objects.create(
            invoice=inv, reason_category=InvoiceVoid.Reason.DATA_ENTRY_ERROR,
            reason="x", maker=self.trusted_user,
        )
        from .exceptions import TrustedBypassBlocked
        with self.assertRaises(TrustedBypassBlocked):
            void.try_trusted_autoapprove()
        self.assertEqual(void.approval_status, ApprovalStatus.PENDING)

    def test_credit_note_never_allows_trusted_bypass(self):
        inv = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user).invoice
        cn = CreditNote(
            original_invoice=inv, amount=Decimal("100000"),
            reason_category=CreditNote.Reason.OVERCHARGE, reason="x",
            maker=self.trusted_user,
        )
        cn.full_clean()
        cn.save()
        from .exceptions import TrustedBypassBlocked
        with self.assertRaises(TrustedBypassBlocked):
            cn.try_trusted_autoapprove()


class VoidWorkflowTests(BillingFixture):
    def test_void_reverses_accrual_and_commission(self):
        inv = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user).invoice
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("500000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.trusted_user)
        # Record a void
        void = InvoiceVoid.objects.create(
            invoice=inv, reason_category=InvoiceVoid.Reason.TENANT_DISPUTE,
            reason="refund agreed", maker=self.maker_user,
            submitted_at=timezone.now(),
        )
        void.approve(self.checker_user)
        execute_void(void, user=self.checker_user)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.VOIDED)
        # Accrual reversed and commission reversed
        self.assertEqual(get_account(SYS_LANDLORD_PAYABLE).balance(), Decimal("0"))
        self.assertEqual(get_account(SYS_COMMISSION_INCOME).balance(), Decimal("0"))
        # AR should also be cleared of the accrual
        self.assertEqual(get_account(SYS_AR_TENANTS).balance(), Decimal("0"))


class CreditNoteBoundsTests(BillingFixture):
    def test_amount_cannot_exceed_invoice_remaining(self):
        inv = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user).invoice
        cn = CreditNote(
            original_invoice=inv, amount=inv.total + Decimal("1"),
            reason_category=CreditNote.Reason.OVERCHARGE, reason="x",
            maker=self.maker_user,
        )
        with self.assertRaises(CreditNoteExceedsInvoice):
            cn.full_clean()

    def test_credit_note_posts_and_reverses_commission(self):
        inv = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user).invoice
        # Apply full payment first so commission is recognised
        payment = Payment.objects.create(
            tenant=self.tenant, amount=inv.total,
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.trusted_user)
        commission_before = get_account(SYS_COMMISSION_INCOME).balance()
        self.assertEqual(commission_before, Decimal("100000"))
        cn = CreditNote.objects.create(
            original_invoice=inv, amount=Decimal("500000"),
            reason_category=CreditNote.Reason.GOODWILL, reason="promo",
            maker=self.maker_user, submitted_at=timezone.now(),
        )
        cn.approve(self.checker_user)
        execute_credit_note(cn, user=self.checker_user)
        cn.refresh_from_db()
        self.assertTrue(cn.number.startswith("CRN-"))
        # Half the commission should have been reversed (proportionally)
        self.assertLess(get_account(SYS_COMMISSION_INCOME).balance(), commission_before)


class RefundRoutingTests(BillingFixture):
    def test_refund_from_held_advance_posts_balanced_journal(self):
        # Create a held advance first
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("300000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.trusted_user)
        # All 300k should sit in managed advance (no invoices exist)
        managed = get_account(SYS_TENANT_ADVANCE_HELD_MANAGED)
        self.assertEqual(managed.balance(), Decimal("300000"))
        refund = Refund.objects.create(
            tenant=self.tenant, tenant_house=self.tenancy_ext,
            amount=Decimal("200000"), method=Refund.Method.CASH,
            source=Refund.Source.HELD_ADVANCE, source_account=managed,
            destination_details="cash to tenant", reference_number="CSH-001",
            reason="exit refund", maker=self.maker_user, submitted_at=timezone.now(),
        )
        refund.approve(self.checker_user)
        execute_refund(refund, user=self.checker_user)
        refund.refresh_from_db()
        self.assertTrue(refund.number.startswith("REF-"))
        self.assertEqual(managed.balance(), Decimal("100000"))


class OverdueSweepTests(BillingFixture):
    def test_marks_due_invoices_overdue(self):
        inv = Invoice.objects.create(
            tenant_house=self.tenancy_ext, period_from=date(2026, 3, 1),
            period_to=date(2026, 3, 31), due_date=date(2026, 3, 14),
            rent_amount=Decimal("1000000"), subtotal=Decimal("1000000"),
            total=Decimal("1000000"),
            status=Invoice.Status.ISSUED, number=allocate_number("INV"),
            issued_at=timezone.now(),
        )
        n = mark_overdue_invoices(today=date(2026, 4, 1))
        inv.refresh_from_db()
        self.assertEqual(n, 1)
        self.assertEqual(inv.status, Invoice.Status.OVERDUE)
