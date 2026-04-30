from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core Entities"

    def ready(self):
        # Wire pre_save signals that cascade Inactive/Status flags from
        # Landlord / Estate / House down to TenantHouse billing schedules.
        from . import signals_inactive  # noqa: F401
