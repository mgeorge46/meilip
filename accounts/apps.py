from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Accounts & Authentication"

    def ready(self):
        # Wire auth signals -> AuditLog
        from . import signals_audit  # noqa: F401
