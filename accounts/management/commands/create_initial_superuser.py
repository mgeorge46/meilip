"""Create an initial superuser for the custom accounts.User model.

Usage:
    python manage.py create_initial_superuser \\
        --email admin@meili.test --phone +256700000000 \\
        --first-name Admin --last-name User --password <pwd>

If the user already exists by email, the command is a no-op.
The user is assigned the SUPER_ADMIN and ADMIN roles.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role, UserRole


class Command(BaseCommand):
    help = "Create an initial superuser and assign SUPER_ADMIN + ADMIN roles."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--phone", required=True)
        parser.add_argument("--first-name", required=True)
        parser.add_argument("--last-name", required=True)
        parser.add_argument("--password", required=True)

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()
        email = opts["email"].lower()
        if User.objects.filter(email=email).exists():
            self.stdout.write(self.style.WARNING(f"User {email} already exists — nothing to do."))
            return

        user = User.objects.create_superuser(
            email=email,
            phone=opts["phone"],
            password=opts["password"],
            first_name=opts["first_name"],
            last_name=opts["last_name"],
        )

        for role_name in ("SUPER_ADMIN", "ADMIN"):
            try:
                role = Role.objects.get(name=role_name)
            except Role.DoesNotExist as exc:
                raise CommandError(
                    f"Role {role_name} missing — run migrations first."
                ) from exc
            UserRole.objects.get_or_create(user=user, role=role, defaults={"is_active": True})

        self.stdout.write(self.style.SUCCESS(f"Created superuser {user.email} with SUPER_ADMIN + ADMIN roles."))
