from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import Account, AccountType, JournalEntry, JournalEntryLine
from accounting.utils import (
    SYS_AR_TENANTS,
    SYS_CASH,
    SYS_COMMISSION_INCOME,
    SYS_LANDLORD_PAYABLE,
    SYS_RENT_INCOME,
    SYS_TENANT_ADVANCE_HELD_MANAGED,
    SYS_TENANT_ADVANCE_HELD_MEILI,
    get_account,
    get_advance_holding_account,
)
from core.models import Estate, House, Landlord


def make_user(suffix):
    User = get_user_model()
    return User.objects.create_user(
        email=f"acct{suffix}@meili.test",
        phone=f"+25670020{suffix:04d}",
        password="pw-long-enough-1",
        first_name=f"Acct{suffix}",
        last_name="Tester",
    )


class ChartOfAccountsSeedTests(TestCase):
    def test_all_required_accounts_present(self):
        required = [
            SYS_CASH,
            SYS_AR_TENANTS,
            SYS_RENT_INCOME,
            SYS_COMMISSION_INCOME,
            SYS_LANDLORD_PAYABLE,
            SYS_TENANT_ADVANCE_HELD_MANAGED,
            SYS_TENANT_ADVANCE_HELD_MEILI,
            "SECURITY_DEPOSIT_HELD",
            "SECURITY_DEPOSIT_REFUNDABLE",
            "TAX_PAYABLE",
            "OWNERS_EQUITY",
            "RETAINED_EARNINGS",
            "MAINTENANCE_REPAIRS",
            "OFFICE_SUPPLIES",
            "SERVICE_COSTS",
        ]
        for sys in required:
            self.assertTrue(
                Account.objects.filter(system_code=sys).exists(),
                f"missing {sys}",
            )

    def test_commission_income_is_standalone(self):
        """Commission Income must not share its account with Rent Income."""
        comm = get_account(SYS_COMMISSION_INCOME)
        rent = get_account(SYS_RENT_INCOME)
        self.assertNotEqual(comm.pk, rent.pk)
        self.assertEqual(comm.account_type.category, "REVENUE")
        self.assertEqual(rent.account_type.category, "REVENUE")
        # Both are leaves, both are postable — but they are DIFFERENT accounts.
        self.assertTrue(comm.is_postable)
        self.assertTrue(rent.is_postable)

    def test_two_advance_accounts_not_merged(self):
        managed = get_account(SYS_TENANT_ADVANCE_HELD_MANAGED)
        meili = get_account(SYS_TENANT_ADVANCE_HELD_MEILI)
        self.assertNotEqual(managed.pk, meili.pk)
        self.assertEqual(managed.account_type.category, "LIABILITY")
        self.assertEqual(meili.account_type.category, "LIABILITY")

    def test_parent_accounts_are_not_postable(self):
        parents = ["1000", "1200", "1300", "2000", "3000", "4000", "5000"]
        for code in parents:
            self.assertFalse(
                Account.objects.get(code=code).is_postable,
                f"{code} should be non-postable",
            )


class AccountHierarchyTests(TestCase):
    def test_parent_must_be_non_postable(self):
        rent = get_account(SYS_RENT_INCOME)  # postable leaf
        acct = Account(
            code="9999",
            name="Bad",
            account_type=AccountType.objects.get(code="REVENUE"),
            parent=rent,
            is_postable=True,
        )
        with self.assertRaises(ValidationError):
            acct.full_clean()

    def test_cannot_be_own_parent(self):
        acct = Account.objects.get(code="1100")
        acct.parent = acct
        with self.assertRaises(ValidationError):
            acct.full_clean()


class JournalEntryValidationTests(TestCase):
    def setUp(self):
        self.cash = get_account(SYS_CASH)
        self.ar = get_account(SYS_AR_TENANTS)
        self.rent = get_account(SYS_RENT_INCOME)
        self.comm = get_account(SYS_COMMISSION_INCOME)
        self.user = make_user(1)

    def _make_entry(self):
        return JournalEntry.objects.create(memo="test", created_by=self.user)

    def test_cannot_post_unbalanced(self):
        je = self._make_entry()
        JournalEntryLine.objects.create(entry=je, account=self.cash, debit=Decimal("100"))
        JournalEntryLine.objects.create(entry=je, account=self.rent, credit=Decimal("80"))
        with self.assertRaises(ValidationError):
            je.post(user=self.user)
        je.refresh_from_db()
        self.assertEqual(je.status, JournalEntry.Status.DRAFT)

    def test_post_balanced(self):
        je = self._make_entry()
        JournalEntryLine.objects.create(entry=je, account=self.cash, debit=Decimal("500"))
        JournalEntryLine.objects.create(entry=je, account=self.rent, credit=Decimal("500"))
        je.post(user=self.user)
        je.refresh_from_db()
        self.assertEqual(je.status, JournalEntry.Status.POSTED)
        self.assertTrue(je.reference.startswith("JE-"))
        self.assertEqual(self.cash.balance(), Decimal("500"))
        self.assertEqual(self.rent.balance(), Decimal("500"))

    def test_cannot_post_to_parent_account(self):
        parent = Account.objects.get(code="1000")
        je = self._make_entry()
        JournalEntryLine.objects.create(entry=je, account=parent, debit=Decimal("100"))
        JournalEntryLine.objects.create(entry=je, account=self.rent, credit=Decimal("100"))
        with self.assertRaises(ValidationError):
            je.post(user=self.user)

    def test_cannot_post_empty_entry(self):
        je = self._make_entry()
        with self.assertRaises(ValidationError):
            je.post(user=self.user)

    def test_cannot_post_twice(self):
        je = self._make_entry()
        JournalEntryLine.objects.create(entry=je, account=self.cash, debit=Decimal("1"))
        JournalEntryLine.objects.create(entry=je, account=self.rent, credit=Decimal("1"))
        je.post(user=self.user)
        with self.assertRaises(ValidationError):
            je.post(user=self.user)

    def test_reverse_posts_offsetting_entry(self):
        je = self._make_entry()
        JournalEntryLine.objects.create(entry=je, account=self.cash, debit=Decimal("300"))
        JournalEntryLine.objects.create(entry=je, account=self.rent, credit=Decimal("300"))
        je.post(user=self.user)
        reversal = je.reverse(user=self.user)
        self.assertEqual(reversal.status, JournalEntry.Status.POSTED)
        self.assertEqual(self.cash.balance(), Decimal("0"))
        self.assertEqual(self.rent.balance(), Decimal("0"))
        je.refresh_from_db()
        self.assertEqual(je.status, JournalEntry.Status.REVERSED)


class CommissionIsolationTests(TestCase):
    def test_commission_balance_untouched_by_rent_posting(self):
        cash = get_account(SYS_CASH)
        rent = get_account(SYS_RENT_INCOME)
        comm = get_account(SYS_COMMISSION_INCOME)
        u = make_user(2)
        je = JournalEntry.objects.create(memo="rent-only", created_by=u)
        JournalEntryLine.objects.create(entry=je, account=cash, debit=Decimal("1000"))
        JournalEntryLine.objects.create(entry=je, account=rent, credit=Decimal("1000"))
        je.post(user=u)
        self.assertEqual(rent.balance(), Decimal("1000"))
        self.assertEqual(comm.balance(), Decimal("0"))


class AdvanceHoldingRouterTests(TestCase):
    def setUp(self):
        self.external_ll = Landlord.objects.create(
            full_name="External LL", phone="+256700300001", is_meili_owned=False
        )
        self.meili_ll = Landlord.objects.create(
            full_name="Meili Co", phone="+256700300002", is_meili_owned=True
        )
        self.managed_estate = Estate.objects.create(landlord=self.external_ll, name="Mgd")
        self.meili_estate = Estate.objects.create(landlord=self.meili_ll, name="Own")
        self.managed_house = House.objects.create(
            estate=self.managed_estate, house_number="M1"
        )
        self.meili_house = House.objects.create(
            estate=self.meili_estate, house_number="O1"
        )

    def test_managed_routes_to_managed_account(self):
        acct = get_advance_holding_account(self.managed_house)
        self.assertEqual(acct.system_code, SYS_TENANT_ADVANCE_HELD_MANAGED)

    def test_meili_routes_to_meili_account(self):
        acct = get_advance_holding_account(self.meili_house)
        self.assertEqual(acct.system_code, SYS_TENANT_ADVANCE_HELD_MEILI)

    def test_house_level_landlord_override_wins(self):
        """If House.landlord overrides Estate.landlord, routing follows house."""
        self.managed_house.landlord = self.meili_ll
        self.managed_house.save(update_fields=["landlord", "updated_at"])
        acct = get_advance_holding_account(self.managed_house)
        self.assertEqual(acct.system_code, SYS_TENANT_ADVANCE_HELD_MEILI)


class JournalEntryLineConstraintTests(TestCase):
    def test_cannot_have_both_debit_and_credit(self):
        je = JournalEntry.objects.create(memo="x")
        line = JournalEntryLine(
            entry=je, account=get_account(SYS_CASH), debit=Decimal("1"), credit=Decimal("1")
        )
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_line_must_have_a_side(self):
        je = JournalEntry.objects.create(memo="x")
        line = JournalEntryLine(entry=je, account=get_account(SYS_CASH))
        with self.assertRaises(ValidationError):
            line.full_clean()
