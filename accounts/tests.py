from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounts.models import Role, UserRole
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
