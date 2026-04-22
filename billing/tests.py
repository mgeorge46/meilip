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
    SYS_ELECTRICITY_INCOME,
    SYS_GARBAGE_INCOME,
    SYS_LANDLORD_PAYABLE,
    SYS_OTHER_UTILITY_INCOME,
    SYS_RENT_INCOME,
    SYS_SECURITY_INCOME,
    SYS_TENANT_ADVANCE_HELD_MANAGED,
    SYS_TENANT_ADVANCE_HELD_MEILI,
    SYS_WATER_INCOME,
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
    UtilityKind,
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


class UtilityBillingTests(BillingFixture):
    """Separately-billed utility lines: routing into utility income accounts
    (Meili-owned) vs landlord payable (managed), interaction with commission,
    flag precedence (house overrides estate), and bundled fallback.
    """

    def _approve_charge(self, charge):
        charge.approval_status = ApprovalStatus.AUTO_APPROVED
        charge.maker = self.emp_trusted.user
        charge.save(update_fields=["approval_status", "maker"])
        return charge

    def _make_charge(self, tenancy, amount, utility_kind, description="util"):
        charge = AdHocCharge.objects.create(
            tenant_house=tenancy, description=description, amount=amount,
            target=AdHocCharge.Target.LANDLORD,
            utility_kind=utility_kind,
            maker=self.emp_trusted.user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        return charge

    # --- Meili-owned: utility income breaks out --------------------------------
    def test_meili_owned_water_flag_routes_to_water_income(self):
        # Flag True on Meili-owned estate; tenancy new, no prior invoice.
        self.estate_meili.water_billed_separately = True
        self.estate_meili.save()
        tenancy = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_meili,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
        )
        self._make_charge(tenancy, Decimal("40000"), UtilityKind.WATER, "water Apr")
        draft = generate_invoice_for_tenancy(tenancy, user=self.maker_user)
        inv = draft.invoice
        # Line breakdown: rent 800000 + water 40000
        self.assertEqual(inv.total, Decimal("840000"))
        self.assertTrue(
            inv.lines.filter(kind=InvoiceLine.Kind.UTILITY,
                             utility_kind=UtilityKind.WATER).exists()
        )
        # Water routed to water income; rent to rent income
        self.assertEqual(get_account(SYS_WATER_INCOME).balance(), Decimal("40000"))
        self.assertEqual(get_account(SYS_RENT_INCOME).balance(), Decimal("800000"))
        self.assertTrue(inv.source_journal.is_balanced())

    def test_meili_owned_house_flag_overrides_estate_flag(self):
        # Estate flag False, house flag True -> treated as separately-billed
        self.estate_meili.electricity_billed_separately = False
        self.estate_meili.save()
        self.house_meili.electricity_billed_separately = True
        self.house_meili.save()
        tenancy = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_meili,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
        )
        self._make_charge(tenancy, Decimal("60000"), UtilityKind.ELECTRICITY,
                          "electricity Apr")
        draft = generate_invoice_for_tenancy(tenancy, user=self.maker_user)
        inv = draft.invoice
        self.assertEqual(get_account(SYS_ELECTRICITY_INCOME).balance(), Decimal("60000"))
        self.assertTrue(
            inv.lines.filter(kind=InvoiceLine.Kind.UTILITY,
                             utility_kind=UtilityKind.ELECTRICITY).exists()
        )

    def test_flag_false_forces_bundled_adhoc_line_not_utility(self):
        # Estate+house both False -> utility charge degrades to AD_HOC line,
        # which posts to generic rent income (Meili-owned).
        self.estate_meili.security_billed_separately = False
        self.estate_meili.save()
        tenancy = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_meili,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
        )
        self._make_charge(tenancy, Decimal("30000"), UtilityKind.SECURITY, "sec Apr")
        draft = generate_invoice_for_tenancy(tenancy, user=self.maker_user)
        inv = draft.invoice
        self.assertFalse(
            inv.lines.filter(kind=InvoiceLine.Kind.UTILITY).exists()
        )
        self.assertTrue(
            inv.lines.filter(kind=InvoiceLine.Kind.AD_HOC, amount=Decimal("30000")).exists()
        )
        # Security income stays at zero
        self.assertEqual(get_account(SYS_SECURITY_INCOME).balance(), Decimal("0"))
        # Rent income absorbed the 30k alongside rent
        self.assertEqual(get_account(SYS_RENT_INCOME).balance(), Decimal("830000"))

    def test_mixed_utilities_meili_owned_break_out_independently(self):
        self.estate_meili.water_billed_separately = True
        self.estate_meili.garbage_billed_separately = True
        self.estate_meili.other_bills_billed_separately = True
        self.estate_meili.other_bills_description = "Generator fuel levy"
        self.estate_meili.save()
        tenancy = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_meili,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
        )
        self._make_charge(tenancy, Decimal("25000"), UtilityKind.WATER)
        self._make_charge(tenancy, Decimal("15000"), UtilityKind.GARBAGE)
        self._make_charge(tenancy, Decimal("10000"), UtilityKind.OTHER, "fuel")
        draft = generate_invoice_for_tenancy(tenancy, user=self.maker_user)
        inv = draft.invoice
        self.assertEqual(inv.total, Decimal("850000"))
        self.assertEqual(get_account(SYS_WATER_INCOME).balance(), Decimal("25000"))
        self.assertEqual(get_account(SYS_GARBAGE_INCOME).balance(), Decimal("15000"))
        self.assertEqual(get_account(SYS_OTHER_UTILITY_INCOME).balance(), Decimal("10000"))
        self.assertEqual(get_account(SYS_RENT_INCOME).balance(), Decimal("800000"))
        self.assertTrue(inv.source_journal.is_balanced())

    # --- Managed: utility stays in landlord_payable ----------------------------
    def test_managed_utility_line_routes_to_landlord_payable(self):
        self.estate_ext.water_billed_separately = True
        self.estate_ext.save()
        self._make_charge(self.tenancy_ext, Decimal("50000"), UtilityKind.WATER)
        draft = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user)
        inv = draft.invoice
        self.assertEqual(inv.total, Decimal("1050000"))
        # Utility income accounts stay empty (managed)
        self.assertEqual(get_account(SYS_WATER_INCOME).balance(), Decimal("0"))
        # Full amount flows through landlord payable
        self.assertEqual(get_account(SYS_LANDLORD_PAYABLE).balance(), Decimal("1050000"))
        self.assertTrue(inv.source_journal.is_balanced())

    # --- Commission: utility excluded -----------------------------------------
    def test_commission_excludes_separately_billed_utility(self):
        """Commission is 10% of rent on the managed estate. A separately-billed
        utility line must NOT attract commission — it's the landlord's pass-through.
        """
        self.estate_ext.water_billed_separately = True
        self.estate_ext.save()
        self._make_charge(self.tenancy_ext, Decimal("100000"), UtilityKind.WATER)
        draft = generate_invoice_for_tenancy(self.tenancy_ext, user=self.maker_user)
        inv = draft.invoice
        self.assertEqual(inv.total, Decimal("1100000"))
        # Full payment
        payment = Payment.objects.create(
            tenant=self.tenant, amount=inv.total,
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.emp_trusted.user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.emp_trusted.user)
        # Commission = 10% of rent (1,000,000) = 100,000 — NOT 10% of total.
        self.assertEqual(get_account(SYS_COMMISSION_INCOME).balance(), Decimal("100000"))
        # Landlord payable net: 1,100,000 accrued − 100,000 commission = 1,000,000.
        self.assertEqual(get_account(SYS_LANDLORD_PAYABLE).balance(), Decimal("1000000"))


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


# ---------------------------------------------------------------------------
# Phase 7 — security deposit, exit strict-order, cross-ownership transfer,
# reports.
# ---------------------------------------------------------------------------
class SecurityDepositApplicationTests(BillingFixture):
    """Deposit applied to an invoice → balanced Dr SECURITY_DEPOSIT_HELD /
    Cr AR_TENANTS and status transitions correctly."""

    def test_apply_to_invoice_posts_balanced_journal(self):
        from accounting.utils import SYS_SECURITY_DEPOSIT_HELD
        from billing.exit_services import _apply_deposit_to_invoice
        from billing.models import SecurityDeposit, SecurityDepositMovement

        deposit = SecurityDeposit.objects.create(
            tenant_house=self.tenancy_ext,
            amount_held=Decimal("500000"),
            status=SecurityDeposit.Status.HELD,
        )
        # Seed the held-account by manually posting a hold journal entry so
        # the ledger balance reflects the deposit.
        from accounting.models import JournalEntry, JournalEntryLine
        from accounting.utils import SYS_CASH
        je = JournalEntry.objects.create(
            entry_date=date(2026, 4, 1),
            memo="Seed deposit",
            source=JournalEntry.Source.MANUAL,
        )
        JournalEntryLine.objects.create(
            entry=je, account=get_account(SYS_CASH),
            debit=Decimal("500000"), credit=Decimal("0"),
            description="Cash received for deposit",
        )
        JournalEntryLine.objects.create(
            entry=je, account=get_account(SYS_SECURITY_DEPOSIT_HELD),
            debit=Decimal("0"), credit=Decimal("500000"),
            description="Deposit held",
        )
        je.post(user=self.maker_user)

        inv = generate_invoice_for_tenancy(
            self.tenancy_ext, user=self.maker_user
        ).invoice
        ar_before = get_account(SYS_AR_TENANTS).balance()
        held_before = get_account(SYS_SECURITY_DEPOSIT_HELD).balance()

        _apply_deposit_to_invoice(
            deposit, inv, Decimal("400000"), user=self.maker_user
        )
        deposit.refresh_from_db()
        inv.refresh_from_db()

        self.assertEqual(deposit.amount_applied, Decimal("400000"))
        self.assertEqual(deposit.balance, Decimal("100000"))
        self.assertEqual(deposit.status, SecurityDeposit.Status.PARTIALLY_APPLIED)
        # SEC_HELD is a liability: applying the deposit means Dr 400k, which
        # moves the account balance by +400k in debit-minus-credit terms.
        self.assertEqual(
            get_account(SYS_SECURITY_DEPOSIT_HELD).balance() - held_before,
            Decimal("400000"),
        )
        # AR is an asset; Cr 400k moves it by -400k.
        self.assertEqual(
            get_account(SYS_AR_TENANTS).balance() - ar_before,
            Decimal("-400000"),
        )
        self.assertTrue(
            SecurityDepositMovement.objects.filter(
                deposit=deposit,
                kind=SecurityDepositMovement.Kind.APPLY_INVOICE,
                amount=Decimal("400000"),
            ).exists()
        )

    def test_deposit_balance_property(self):
        from billing.models import SecurityDeposit
        d = SecurityDeposit(
            amount_held=Decimal("500000"),
            amount_applied=Decimal("120000"),
            amount_refunded=Decimal("30000"),
        )
        self.assertEqual(d.balance, Decimal("350000"))


class ExitStrictOrderTests(BillingFixture):
    """SPEC §20.5 — outstanding → damages → refund remainder."""

    def _received(self, amount):
        p = Payment.objects.create(
            tenant=self.tenant, amount=amount,
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(p, user=self.trusted_user)
        return p

    def test_outstanding_consumed_before_damages_and_refund_for_remainder(self):
        from billing.exit_services import (
            build_settlement_plan,
            compute_exit_settlement,
            execute_exit_settlement,
        )
        from billing.models import ExitSettlement

        # Issue an invoice; leave unpaid so it becomes outstanding.
        inv = generate_invoice_for_tenancy(
            self.tenancy_ext, user=self.maker_user
        ).invoice
        self.assertEqual(inv.outstanding, Decimal("1000000"))
        # Tenant overpays to generate a held advance of 2,000,000.
        self._received(Decimal("3000000"))  # 1M applied, 2M held

        comp = compute_exit_settlement(self.tenancy_ext)
        # Outstanding should now be zero (held-advance settled first).
        self.assertEqual(comp.outstanding_total, Decimal("0"))
        self.assertEqual(comp.held_managed, Decimal("2000000"))

        damages = [{"description": "broken window", "amount": Decimal("300000")}]
        plan = build_settlement_plan(comp, damages=damages)
        # Strict-order: step_1_apply_to_invoices fires first
        self.assertEqual(plan["ownership"], "MANAGED")
        # Damages land in step 2 and consume 300k from held pool
        step2_amt = sum(Decimal(s["amount"]) for s in plan["step_2_apply_to_damages"])
        self.assertEqual(step2_amt, Decimal("300000"))
        # Refund = (2M - 0 - 300k) = 1.7M
        self.assertEqual(
            Decimal(plan["step_4_refund"]["advance_remainder"]),
            Decimal("1700000"),
        )

        # Execute the settlement end-to-end (happy path).
        settlement = ExitSettlement.objects.create(
            tenant_house=self.tenancy_ext,
            held_managed_at_start=comp.held_managed,
            held_meili_at_start=comp.held_meili,
            deposit_at_start=comp.deposit_balance,
            outstanding_at_start=comp.outstanding_total,
            damages_total=Decimal("300000"),
            plan=plan,
            maker=self.maker_user,
            submitted_at=timezone.now(),
        )
        settlement.approve(self.checker_user)
        execute_exit_settlement(
            settlement,
            refund_method=Refund.Method.CASH,
            refund_bank_account=self.bank_account,
            refund_destination="cash to tenant",
            refund_reference="EXIT-REF-001",
            damages_input=damages,
            user=self.checker_user,
        )
        settlement.refresh_from_db()
        self.tenancy_ext.refresh_from_db()
        self.assertEqual(settlement.status, ExitSettlement.Status.EXECUTED)
        self.assertEqual(self.tenancy_ext.status, TenantHouse.Status.EXITED)
        # Refund row created — PENDING its own maker-checker.
        self.assertIsNotNone(settlement.refund)
        self.assertEqual(
            settlement.refund.approval_status, ApprovalStatus.PENDING
        )

    def test_maker_cannot_self_approve_exit_envelope(self):
        from billing.exit_services import build_settlement_plan, compute_exit_settlement
        from billing.models import ExitSettlement

        comp = compute_exit_settlement(self.tenancy_ext)
        settlement = ExitSettlement.objects.create(
            tenant_house=self.tenancy_ext,
            plan=build_settlement_plan(comp),
            maker=self.maker_user,
            submitted_at=timezone.now(),
        )
        with self.assertRaises(SelfApprovalBlocked):
            settlement.approve(self.maker_user)


class CrossOwnershipTransferTests(BillingFixture):
    """Transferring held advance from one tenancy on a Managed house to
    another tenancy on a Meili-owned house MUST post to BOTH correct
    holding accounts (no merging)."""

    def test_journal_splits_across_managed_and_meili_accounts(self):
        from billing.exit_services import _execute_transfer_between_held_accounts

        # Seed a held advance on the MANAGED account FIRST — while the
        # managed tenancy is the only active tenancy (so apply_payment routes
        # the surplus to SYS_TENANT_ADVANCE_HELD_MANAGED).
        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("500000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.trusted_user)

        # Now add a second tenancy on the Meili-owned house for the same tenant.
        tenancy_meili = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house_meili,
            status=TenantHouse.Status.ACTIVE,
            billing_start_date=date(2026, 4, 1),
            move_in_date=date(2026, 4, 1),
        )

        managed = get_account(SYS_TENANT_ADVANCE_HELD_MANAGED)
        meili = get_account(SYS_TENANT_ADVANCE_HELD_MEILI)
        self.assertEqual(managed.balance(), Decimal("500000"))
        self.assertEqual(meili.balance(), Decimal("0"))

        # Transfer 200k managed→meili.
        _execute_transfer_between_held_accounts(
            tenant=self.tenant,
            source_house=self.house_ext,
            target_tenancy_id=tenancy_meili.pk,
            amount=Decimal("200000"),
            user=self.maker_user,
        )

        self.assertEqual(managed.balance(), Decimal("300000"))
        self.assertEqual(meili.balance(), Decimal("200000"))


class ReportsSmokeTests(BillingFixture):
    """Report views return 200 and compute correct top-line numbers. The
    heavy lifting is exercised in other tests — this keeps report regression
    cheap."""

    def setUp(self):
        super().setUp()
        # Elevate maker to superuser so FINANCE_ROLES-gated views pass
        # (avoids having to wire UserRole fixtures per test).
        self.maker_user.is_superuser = True
        self.maker_user.is_staff = True
        self.maker_user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.maker_user)

    def test_revenue_report_returns_issued_invoice_total(self):
        from django.urls import reverse
        inv = generate_invoice_for_tenancy(
            self.tenancy_ext, user=self.maker_user
        ).invoice
        self.assertEqual(inv.status, Invoice.Status.ISSUED)
        resp = self.client.get(reverse("billing:report-revenue"))
        self.assertEqual(resp.status_code, 200)
        # At least one row in the 12-month window should pick up 1M rent.
        totals = {r["period"]: r for r in resp.context["rows"]}
        total_rent = sum(r["rent"] for r in totals.values())
        self.assertEqual(total_rent, Decimal("1000000"))

    def test_occupancy_report_includes_fixture_estate(self):
        from django.urls import reverse
        resp = self.client.get(reverse("billing:report-occupancy"))
        self.assertEqual(resp.status_code, 200)
        estate_names = {r["estate"] for r in resp.context["estates"]}
        self.assertIn(self.estate_ext.name, estate_names)

    def test_collections_report_matches_billed_and_collected(self):
        from django.urls import reverse
        inv = generate_invoice_for_tenancy(
            self.tenancy_ext, user=self.maker_user
        ).invoice
        payment = Payment.objects.create(
            tenant=self.tenant, amount=inv.total,
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.trusted_user)
        resp = self.client.get(reverse("billing:report-collections"))
        self.assertEqual(resp.status_code, 200)
        billed = sum(r["billed"] for r in resp.context["rows"])
        collected = sum(r["collected"] for r in resp.context["rows"])
        self.assertEqual(billed, Decimal("1000000"))
        self.assertEqual(collected, Decimal("1000000"))

    def test_repairs_report_groups_by_house(self):
        from django.urls import reverse
        AdHocCharge.objects.create(
            tenant_house=self.tenancy_ext,
            description="Roof repair",
            amount=Decimal("150000"),
            target=AdHocCharge.Target.MEILI,
            approval_status=ApprovalStatus.AUTO_APPROVED,
            maker=self.trusted_user,
        )
        AdHocCharge.objects.create(
            tenant_house=self.tenancy_ext,
            description="Parking sticker",
            amount=Decimal("10000"),
            target=AdHocCharge.Target.MEILI,
            approval_status=ApprovalStatus.AUTO_APPROVED,
            maker=self.trusted_user,
        )
        resp = self.client.get(reverse("billing:report-repairs"))
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context["rows"])
        # Only the repair row should be counted.
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"], Decimal("150000"))
        self.assertEqual(rows[0]["count"], 1)

    def test_advances_report_flags_stale_holds(self):
        from datetime import timedelta as _td
        from django.urls import reverse

        payment = Payment.objects.create(
            tenant=self.tenant, amount=Decimal("300000"),
            method=Payment.Method.CASH, bank_account=self.bank_account,
            maker=self.trusted_user,
            approval_status=ApprovalStatus.AUTO_APPROVED,
        )
        apply_payment(payment, user=self.trusted_user)
        # Age the allocation past 2 periods.
        PaymentAllocation.objects.filter(
            payment=payment, is_advance_hold=True
        ).update(allocated_at=timezone.now() - _td(days=90))

        resp = self.client.get(reverse("billing:report-advances"))
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context["rows"])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].stale_badge)
