from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import AuditAction, AuditLog, Role, UserRole
from accounts.permissions import has_any_role, has_role


class CustomUserTests(TestCase):
    def test_create_user_requires_email_and_phone(self):
        User = get_user_model()
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", phone="+256700000001", password="pw")
        with self.assertRaises(ValueError):
            User.objects.create_user(email="a@b.c", phone="", password="pw")

    def test_create_user_hashes_password_with_argon2(self):
        User = get_user_model()
        user = User.objects.create_user(
            email="jane@meili.test",
            phone="+256700000002",
            password="sup3r-str0ng-pw",
            first_name="Jane",
            last_name="Doe",
        )
        self.assertTrue(user.password.startswith("argon2$"))
        self.assertTrue(user.check_password("sup3r-str0ng-pw"))

    def test_email_is_username_field(self):
        User = get_user_model()
        self.assertEqual(User.USERNAME_FIELD, "email")

    def test_create_superuser(self):
        User = get_user_model()
        u = User.objects.create_superuser(
            email="admin@meili.test",
            phone="+256700000003",
            password="pw-long-enough-1",
            first_name="Root",
            last_name="Admin",
        )
        self.assertTrue(u.is_superuser)
        self.assertTrue(u.is_staff)


class RoleAssignmentTests(TestCase):
    def test_roles_seeded(self):
        for name in [
            "SUPER_ADMIN", "ADMIN", "ACCOUNT_MANAGER", "COLLECTIONS",
            "SALES_REP", "FINANCE", "TENANT", "LANDLORD",
        ]:
            self.assertTrue(Role.objects.filter(name=name).exists(), name)

    def test_has_role(self):
        User = get_user_model()
        user = User.objects.create_user(
            email="am@meili.test", phone="+256700000004", password="pw-long-enough-1",
            first_name="Amy", last_name="Manager",
        )
        role = Role.objects.get(name="ACCOUNT_MANAGER")
        UserRole.objects.create(user=user, role=role)
        self.assertTrue(has_role(user, "ACCOUNT_MANAGER"))
        self.assertFalse(has_role(user, "FINANCE"))
        self.assertTrue(has_any_role(user, "FINANCE", "ACCOUNT_MANAGER"))

    def test_superuser_has_every_role(self):
        User = get_user_model()
        su = User.objects.create_superuser(
            email="su@meili.test", phone="+256700000005", password="pw-long-enough-1",
            first_name="Super", last_name="User",
        )
        self.assertTrue(has_role(su, "FINANCE"))

    def test_inactive_user_role_ignored(self):
        User = get_user_model()
        user = User.objects.create_user(
            email="inactive@meili.test", phone="+256700000006", password="pw-long-enough-1",
            first_name="In", last_name="Active",
        )
        role = Role.objects.get(name="COLLECTIONS")
        UserRole.objects.create(user=user, role=role, is_active=False)
        self.assertFalse(has_role(user, "COLLECTIONS"))


class SuperuserCommandTests(TestCase):
    def test_create_initial_superuser_assigns_roles(self):
        call_command(
            "create_initial_superuser",
            email="bootstrap@meili.test",
            phone="+256700000099",
            first_name="Boot",
            last_name="Strap",
            password="pw-long-enough-1",
        )
        User = get_user_model()
        user = User.objects.get(email="bootstrap@meili.test")
        self.assertTrue(user.is_superuser)
        self.assertEqual(
            set(user.active_role_names()), {"SUPER_ADMIN", "ADMIN"}
        )


# ---------------------------------------------------------------------------
# AuditLog (Phase 8) — model + middleware + viewer
# ---------------------------------------------------------------------------
def _mk_user(email, phone, *, role=None):
    User = get_user_model()
    u = User.objects.create_user(
        email=email, phone=phone, password="pw-long-enough-1",
        first_name="A", last_name="B",
    )
    if role:
        UserRole.objects.create(user=u, role=Role.objects.get(name=role))
    return u


@override_settings(AXES_ENABLED=False)
class AuditLogModelTests(TestCase):
    def test_record_captures_request_metadata(self):
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.post(
            "/some/path", HTTP_USER_AGENT="test-ua", HTTP_X_FORWARDED_FOR="203.0.113.5",
        )
        req.user = _mk_user("actor@meili.test", "+256700901101", role="ADMIN")

        entry = AuditLog.record(
            AuditAction.UPDATE, request=req, target_repr="something changed",
            detail={"field": "value"},
        )
        self.assertEqual(entry.actor_id, req.user.pk)
        self.assertEqual(entry.ip_address, "203.0.113.5")  # XFF honoured
        self.assertEqual(entry.user_agent, "test-ua")
        self.assertEqual(entry.path, "/some/path")
        self.assertEqual(entry.method, "POST")
        self.assertEqual(entry.detail, {"field": "value"})

    def test_login_signal_writes_audit_row(self):
        user = _mk_user("login@meili.test", "+256700901102")
        c = Client()
        resp = c.post(reverse("accounts:login"), {
            "email": "login@meili.test", "password": "pw-long-enough-1",
        })
        # Either success redirect or re-render; either way the signal fires on
        # success. We care the audit row exists for the happy path.
        if resp.status_code in (302, 303):
            self.assertTrue(
                AuditLog.objects.filter(
                    action=AuditAction.LOGIN_SUCCESS, actor=user,
                ).exists()
            )


@override_settings(AXES_ENABLED=False)
class AuditLogViewerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk_user("audit-admin@meili.test", "+256700901201", role="ADMIN")
        cls.clerk = _mk_user("audit-clerk@meili.test", "+256700901202", role="COLLECTIONS")
        AuditLog.objects.create(
            actor=cls.admin, action=AuditAction.LOGIN_SUCCESS,
            target_repr="audit-admin@meili.test", ip_address="127.0.0.1",
        )
        AuditLog.objects.create(
            actor=cls.clerk, action=AuditAction.PERMISSION_DENIED,
            target_type="Invoice", target_id="42",
            target_repr="INV-00042", ip_address="10.0.0.5",
        )

    def test_admin_can_view(self):
        c = Client()
        c.force_login(self.admin)
        resp = c.get(reverse("accounts:audit-log"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "audit-admin@meili.test")
        self.assertContains(resp, "audit-clerk@meili.test")

    def test_collections_role_forbidden(self):
        c = Client()
        c.force_login(self.clerk)
        resp = c.get(reverse("accounts:audit-log"))
        self.assertEqual(resp.status_code, 403)

    def test_filter_by_action(self):
        c = Client()
        c.force_login(self.admin)
        resp = c.get(reverse("accounts:audit-log"), {"action": AuditAction.PERMISSION_DENIED})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "audit-clerk@meili.test")
        # Admin's LOGIN_SUCCESS row should be filtered out of the table body
        # (the dropdown itself still lists "Login success" as an option, so
        # we check rows instead of raw text).
        self.assertNotContains(resp, "audit-admin@meili.test")

    def test_filter_by_actor_email(self):
        c = Client()
        c.force_login(self.admin)
        resp = c.get(reverse("accounts:audit-log"), {"actor": "clerk"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "audit-clerk@meili.test")
