from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.fields import UGXField, USDField
from core.models import (
    BillingCycle,
    Currency,
    Estate,
    House,
    Landlord,
    Tenant,
    TenantHouse,
)
from core.utils import get_effective_setting


class MoneyFieldTests(TestCase):
    def test_ugx_rejects_decimals(self):
        f = UGXField()
        with self.assertRaises(ValidationError):
            f.to_python(Decimal("100.50"))

    def test_ugx_accepts_whole_numbers(self):
        f = UGXField()
        self.assertEqual(f.to_python(100), Decimal("100"))
        self.assertEqual(f.to_python("2000000"), Decimal("2000000"))

    def test_usd_rounds_to_two_dp(self):
        f = USDField()
        self.assertEqual(f.to_python(Decimal("10.126")), Decimal("10.13"))
        self.assertEqual(f.to_python("3.555"), Decimal("3.56"))
        self.assertEqual(f.to_python(Decimal("1")), Decimal("1.00"))

    def test_usd_handles_none(self):
        self.assertIsNone(USDField().to_python(None))
        self.assertIsNone(UGXField().to_python(""))


class SeededReferenceDataTests(TestCase):
    def test_currencies_seeded(self):
        ugx = Currency.objects.get(code="UGX")
        usd = Currency.objects.get(code="USD")
        self.assertTrue(ugx.is_primary)
        self.assertFalse(usd.is_primary)

    def test_billing_cycles_seeded(self):
        self.assertGreaterEqual(BillingCycle.objects.count(), 7)


def make_user(suffix):
    User = get_user_model()
    return User.objects.create_user(
        email=f"u{suffix}@meili.test",
        phone=f"+2567000001{suffix:02d}",
        password="pw-long-enough-1",
        first_name=f"U{suffix}",
        last_name="Test",
    )


class SoftDeleteTests(TestCase):
    def test_soft_delete_hides_from_default_manager(self):
        ll = Landlord.objects.create(full_name="Alice Ll", phone="+256700100001")
        self.assertIn(ll, Landlord.objects.all())
        ll.soft_delete()
        self.assertNotIn(ll, Landlord.objects.all())
        self.assertIn(ll, Landlord.all_objects.all())
        self.assertTrue(ll.is_deleted)
        self.assertIsNotNone(ll.deleted_at)


class EffectiveSettingTests(TestCase):
    def setUp(self):
        self.ugx = Currency.objects.get(code="UGX")
        self.monthly = BillingCycle.objects.get(name="Monthly")
        self.weekly = BillingCycle.objects.get(name="Weekly")
        self.landlord = Landlord.objects.create(full_name="Bob Ll", phone="+256700100002")
        self.estate = Estate.objects.create(
            landlord=self.landlord,
            name="Kira Estate",
            currency=self.ugx,
            billing_cycle=self.monthly,
        )

    def test_house_override_wins(self):
        house = House.objects.create(
            estate=self.estate,
            house_number="H1",
            billing_cycle=self.weekly,
        )
        self.assertEqual(get_effective_setting(house, "billing_cycle"), self.weekly)

    def test_fallback_to_estate_when_house_null(self):
        house = House.objects.create(estate=self.estate, house_number="H2")
        self.assertEqual(get_effective_setting(house, "billing_cycle"), self.monthly)
        self.assertEqual(get_effective_setting(house, "currency"), self.ugx)

    def test_none_when_neither_has_value(self):
        house = House.objects.create(estate=self.estate, house_number="H3")
        self.assertIsNone(get_effective_setting(house, "tax_type"))


class TenantHouseM2MTests(TestCase):
    def setUp(self):
        self.landlord = Landlord.objects.create(full_name="Carol Ll", phone="+256700100003")
        self.estate = Estate.objects.create(landlord=self.landlord, name="Bugolobi")
        self.h_a = House.objects.create(estate=self.estate, house_number="A1")
        self.h_b = House.objects.create(estate=self.estate, house_number="B1")
        self.tenant = Tenant.objects.create(full_name="Tom Tenant", phone="+256700100004")

    def test_tenant_can_have_multiple_tenancies(self):
        TenantHouse.objects.create(
            tenant=self.tenant, house=self.h_a, status=TenantHouse.Status.ACTIVE
        )
        TenantHouse.objects.create(
            tenant=self.tenant, house=self.h_b, status=TenantHouse.Status.PROSPECT
        )
        self.assertEqual(self.tenant.tenancies.count(), 2)
        self.assertEqual(self.tenant.derived_status, "Active")

    def test_derived_status_prospect_only(self):
        TenantHouse.objects.create(
            tenant=self.tenant, house=self.h_a, status=TenantHouse.Status.PROSPECT
        )
        self.assertEqual(self.tenant.derived_status, "Prospect Only")

    def test_derived_status_exited(self):
        TenantHouse.objects.create(
            tenant=self.tenant, house=self.h_a, status=TenantHouse.Status.EXITED
        )
        self.assertEqual(self.tenant.derived_status, "Exited")


class UGXModelFieldTests(TestCase):
    def test_ugx_field_on_model_rejects_decimal(self):
        landlord = Landlord.objects.create(full_name="Dana Ll", phone="+256700100005")
        estate = Estate.objects.create(landlord=landlord, name="Naalya")
        house = House(estate=estate, house_number="X1", periodic_rent=Decimal("100.50"))
        with self.assertRaises(ValidationError):
            house.full_clean()

    def test_ugx_field_saves_whole_number(self):
        landlord = Landlord.objects.create(full_name="Eve Ll", phone="+256700100006")
        estate = Estate.objects.create(landlord=landlord, name="Muyenga")
        house = House.objects.create(
            estate=estate, house_number="Y1", periodic_rent=Decimal("2000000")
        )
        house.refresh_from_db()
        self.assertEqual(house.periodic_rent, Decimal("2000000"))
