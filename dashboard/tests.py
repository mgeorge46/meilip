"""Tests for Phase 3 dashboard/core CRUD: permission boundaries, derived
statuses, tenancy lifecycle, profile edit guard."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, UserRole
from core.models import Estate, House, Landlord, Tenant, TenantHouse

User = get_user_model()


def _mk_user(email, phone):
    return User.objects.create_user(
        email=email, phone=phone, password="pw-long-enough-1", first_name="A", last_name="B"
    )


def _assign(user, role_name):
    role = Role.objects.get(name=role_name)
    return UserRole.objects.create(user=user, role=role)


# Disable axes during test runs to avoid lockout interference
@override_settings(AXES_ENABLED=False)
class PermissionBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk_user("admin@meili.test", "+256700900001")
        _assign(cls.admin, "ADMIN")
        cls.collections = _mk_user("coll@meili.test", "+256700900002")
        _assign(cls.collections, "COLLECTIONS")
        cls.tenant_user = _mk_user("ten@meili.test", "+256700900003")
        _assign(cls.tenant_user, "TENANT")
        cls.outsider = _mk_user("out@meili.test", "+256700900004")

    def test_unauth_redirected_to_login(self):
        c = Client()
        resp = c.get(reverse("core:tenant-list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_authenticated_without_role_is_forbidden(self):
        c = Client()
        c.force_login(self.outsider)
        resp = c.get(reverse("core:tenant-list"))
        self.assertEqual(resp.status_code, 403)

    def test_tenant_role_blocked_from_tenant_list(self):
        c = Client()
        c.force_login(self.tenant_user)
        resp = c.get(reverse("core:tenant-list"))
        self.assertEqual(resp.status_code, 403)

    def test_collections_can_view_tenant_list(self):
        c = Client()
        c.force_login(self.collections)
        resp = c.get(reverse("core:tenant-list"))
        self.assertEqual(resp.status_code, 200)

    def test_collections_cannot_create_landlord(self):
        """Landlord creation is restricted to ADMIN / ACCOUNT_MANAGER."""
        c = Client()
        c.force_login(self.collections)
        resp = c.get(reverse("core:landlord-create"))
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_reach_employee_list(self):
        c = Client()
        c.force_login(self.admin)
        resp = c.get(reverse("core:employee-list"))
        self.assertEqual(resp.status_code, 200)

    def test_non_admin_blocked_from_employee_list(self):
        c = Client()
        c.force_login(self.collections)
        resp = c.get(reverse("core:employee-list"))
        self.assertEqual(resp.status_code, 403)


@override_settings(AXES_ENABLED=False)
class TenancyLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk_user("admin2@meili.test", "+256700900010")
        _assign(cls.admin, "ADMIN")
        cls.landlord = Landlord.objects.create(full_name="LL", phone="+256700900011")
        cls.estate = Estate.objects.create(landlord=cls.landlord, name="E1")
        cls.house = House.objects.create(estate=cls.estate, house_number="1")
        cls.tenant = Tenant.objects.create(full_name="Tenant X", phone="+256700900012")

    def test_prospect_activation_marks_house_occupied(self):
        th = TenantHouse.objects.create(tenant=self.tenant, house=self.house)
        self.assertEqual(th.status, TenantHouse.Status.PROSPECT)
        c = Client()
        c.force_login(self.admin)
        resp = c.post(reverse("core:tenancy-activate", args=[th.pk]))
        self.assertEqual(resp.status_code, 302)
        th.refresh_from_db()
        self.house.refresh_from_db()
        self.assertEqual(th.status, TenantHouse.Status.ACTIVE)
        self.assertIsNotNone(th.move_in_date)
        self.assertEqual(self.house.occupancy_status, House.Occupancy.OCCUPIED)

    def test_exit_marks_house_vacant_when_no_other_active(self):
        th = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house, status=TenantHouse.Status.ACTIVE
        )
        self.house.occupancy_status = House.Occupancy.OCCUPIED
        self.house.save()
        c = Client()
        c.force_login(self.admin)
        resp = c.post(reverse("core:tenancy-exit", args=[th.pk]))
        self.assertEqual(resp.status_code, 302)
        th.refresh_from_db()
        self.house.refresh_from_db()
        self.assertEqual(th.status, TenantHouse.Status.EXITED)
        self.assertEqual(self.house.occupancy_status, House.Occupancy.VACANT)

    def test_exit_keeps_house_occupied_when_other_active_remains(self):
        th_exit = TenantHouse.objects.create(
            tenant=self.tenant, house=self.house, status=TenantHouse.Status.ACTIVE
        )
        tenant2 = Tenant.objects.create(full_name="Tenant Y", phone="+256700900013")
        TenantHouse.objects.create(
            tenant=tenant2, house=self.house, status=TenantHouse.Status.ACTIVE
        )
        self.house.occupancy_status = House.Occupancy.OCCUPIED
        self.house.save()
        c = Client()
        c.force_login(self.admin)
        c.post(reverse("core:tenancy-exit", args=[th_exit.pk]))
        self.house.refresh_from_db()
        self.assertEqual(self.house.occupancy_status, House.Occupancy.OCCUPIED)

    def test_derived_status_transitions(self):
        th = TenantHouse.objects.create(tenant=self.tenant, house=self.house)
        self.assertEqual(self.tenant.derived_status, "Prospect Only")
        th.status = TenantHouse.Status.ACTIVE
        th.save()
        self.assertEqual(self.tenant.derived_status, "Active")
        th.status = TenantHouse.Status.EXITED
        th.save()
        self.assertEqual(self.tenant.derived_status, "Exited")


@override_settings(AXES_ENABLED=False)
class ProfileGuardTests(TestCase):
    """Tenants and landlords cannot edit their own accounts profile page."""

    def test_tenant_cannot_post_to_profile(self):
        u = _mk_user("t@meili.test", "+256700900021")
        _assign(u, "TENANT")
        c = Client()
        c.force_login(u)
        resp = c.post(reverse("accounts:profile"), {
            "first_name": "Changed", "last_name": "Name", "phone": "+256700900021",
        })
        # The view redirects with an error message rather than saving.
        self.assertEqual(resp.status_code, 302)
        u.refresh_from_db()
        self.assertNotEqual(u.first_name, "Changed")

    def test_employee_can_post_to_profile(self):
        u = _mk_user("e@meili.test", "+256700900022")
        _assign(u, "ACCOUNT_MANAGER")
        c = Client()
        c.force_login(u)
        resp = c.post(reverse("accounts:profile"), {
            "first_name": "NewFirst", "last_name": "NewLast", "phone": "+256700900022",
        })
        self.assertEqual(resp.status_code, 302)
        u.refresh_from_db()
        self.assertEqual(u.first_name, "NewFirst")


@override_settings(AXES_ENABLED=False)
class TenantUpdateGuardTests(TestCase):
    """A tenant user cannot edit their own Tenant record via the CRUD form."""

    def test_tenant_self_edit_blocked(self):
        u = _mk_user("tu@meili.test", "+256700900031")
        _assign(u, "TENANT")
        t = Tenant.objects.create(user=u, full_name="Self", phone="+256700900031")
        c = Client()
        c.force_login(u)
        resp = c.get(reverse("core:tenant-update", args=[t.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_edit_any_tenant(self):
        admin = _mk_user("adm@meili.test", "+256700900032")
        _assign(admin, "ADMIN")
        t = Tenant.objects.create(full_name="Anon", phone="+256700900033")
        c = Client()
        c.force_login(admin)
        resp = c.get(reverse("core:tenant-update", args=[t.pk]))
        self.assertEqual(resp.status_code, 200)


@override_settings(AXES_ENABLED=False)
class ComingSoonAndSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _mk_user("s@meili.test", "+256700900041")
        _assign(cls.user, "ADMIN")

    def test_coming_soon_renders(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get(reverse("dashboard:coming-soon", args=["invoices"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invoices")

    def test_search_empty(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get(reverse("dashboard:search"))
        self.assertEqual(resp.status_code, 200)

    def test_search_with_query(self):
        Tenant.objects.create(full_name="Findable Person", phone="+256700900099")
        c = Client()
        c.force_login(self.user)
        resp = c.get(reverse("dashboard:search"), {"q": "Findable"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Findable Person")
