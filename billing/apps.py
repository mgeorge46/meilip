from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"
    verbose_name = "Billing"

    def ready(self):
        # Wire post_save signals that post GL journals when LandlordPayout /
        # SupplierPayment become effectively approved.
        from . import signals_gl  # noqa: F401
