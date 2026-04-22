import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from .managers import UserManager


PHONE_VALIDATOR = RegexValidator(
    regex=r"^\+\d{9,15}$",
    message="Phone must be in E.164 format, e.g. +256712345678",
)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=16, unique=True, validators=[PHONE_VALIDATOR])
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    profile_picture = models.ImageField(upload_to="profile_pics/", null=True, blank=True)
    force_password_change = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["phone", "first_name", "last_name"]

    history = HistoricalRecords()

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return f"{self.get_full_name()} <{self.email}>"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    @property
    def initials(self):
        return (self.first_name[:1] + self.last_name[:1]).upper()

    def active_role_names(self):
        return list(
            self.user_roles.filter(is_active=True, role__is_active=True)
            .values_list("role__name", flat=True)
        )


class Role(models.Model):
    class Name(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
        ADMIN = "ADMIN", "Admin"
        ACCOUNT_MANAGER = "ACCOUNT_MANAGER", "Account Manager"
        COLLECTIONS = "COLLECTIONS", "Collections"
        SALES_REP = "SALES_REP", "Sales Rep"
        FINANCE = "FINANCE", "Finance"
        TENANT = "TENANT", "Tenant"
        LANDLORD = "LANDLORD", "Landlord"

    name = models.CharField(max_length=32, choices=Name.choices, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_system = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.get_name_display()


class UserRole(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_roles",
    )
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="user_roles")
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="role_assignments_made",
    )
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        unique_together = ("user", "role")
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.user.email} -> {self.role.name}"


class LoginAttempt(models.Model):
    email = models.EmailField(db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    success = models.BooleanField(default=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    failure_reason = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["email", "-timestamp"])]

    def __str__(self):
        return f"{self.email} {'OK' if self.success else 'FAIL'} @ {self.timestamp:%Y-%m-%d %H:%M}"


def _token_expiry_default():
    return timezone.now() + timedelta(minutes=30)


class PasswordResetToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_token_expiry_default)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()

    def __str__(self):
        return f"Reset token for {self.user.email}"


# ---------------------------------------------------------------------------
# Audit log — complements simple-history with a high-level, append-only
# per-action record that includes IP + User-Agent. simple-history answers
# "what changed on this row?"; AuditLog answers "who did what action, from
# where, when?" and is the log shown to Admins in the audit viewer.
# Never deleted. Admin/Super-Admin read-only.
# ---------------------------------------------------------------------------
class AuditAction(models.TextChoices):
    LOGIN_SUCCESS = "LOGIN_SUCCESS", "Login success"
    LOGIN_FAILED = "LOGIN_FAILED", "Login failed"
    LOGOUT = "LOGOUT", "Logout"
    PASSWORD_RESET = "PASSWORD_RESET", "Password reset"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Password changed"
    PERMISSION_DENIED = "PERMISSION_DENIED", "Permission denied"
    CREATE = "CREATE", "Create"
    UPDATE = "UPDATE", "Update"
    DELETE = "DELETE", "Delete"
    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"
    VOID = "VOID", "Void"
    REFUND = "REFUND", "Refund"
    CREDIT_NOTE = "CREDIT_NOTE", "Credit note"
    PAYMENT = "PAYMENT", "Payment"
    EXPORT = "EXPORT", "Export"
    API_CALL = "API_CALL", "API call"
    NOTIFICATION_SENT = "NOTIFICATION_SENT", "Notification sent"
    OTHER = "OTHER", "Other"


class AuditLog(models.Model):
    """Append-only audit trail. Intentionally a flat model to keep writes
    cheap and queries simple.

    `actor` is null for system-driven events (e.g. Celery-triggered invoice
    generation) or for failed login attempts where no user can be resolved.
    `target_repr` stores a human-readable label; `target_type` / `target_id`
    allow filtering in the admin viewer without pulling ContentType in.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, db_index=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    target_repr = models.CharField(max_length=255, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    path = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=8, blank=True)

    detail = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["actor", "-timestamp"]),
            models.Index(fields=["action", "-timestamp"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        who = self.actor.email if self.actor else "-"
        tgt = f"{self.target_type}#{self.target_id}" if self.target_type else "-"
        return f"{self.timestamp:%Y-%m-%d %H:%M} {who} {self.action} {tgt}"

    @classmethod
    def record(cls, action, actor=None, target=None, request=None, detail=None, target_repr=""):
        """Convenience writer — resolves target + request automatically."""
        from django.db.models import Model as _Model
        kwargs = {
            "action": action,
            "actor": actor if (actor and getattr(actor, "pk", None)) else None,
            "detail": detail or {},
        }
        if target is not None and isinstance(target, _Model):
            kwargs["target_type"] = target.__class__.__name__
            kwargs["target_id"] = str(getattr(target, "pk", "") or "")
            kwargs["target_repr"] = target_repr or str(target)[:255]
        elif target_repr:
            kwargs["target_repr"] = target_repr[:255]
        if request is not None:
            kwargs["ip_address"] = _extract_ip(request)
            kwargs["user_agent"] = (request.META.get("HTTP_USER_AGENT") or "")[:512]
            kwargs["path"] = (getattr(request, "path", "") or "")[:255]
            kwargs["method"] = (getattr(request, "method", "") or "")[:8]
            if kwargs["actor"] is None:
                u = getattr(request, "user", None)
                if u is not None and getattr(u, "is_authenticated", False):
                    kwargs["actor"] = u
        return cls.objects.create(**kwargs)


def _extract_ip(request):
    """Honour X-Forwarded-For when behind the nginx reverse proxy, else
    fall back to REMOTE_ADDR."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None
