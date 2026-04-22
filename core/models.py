"""Core entities — abstract base, Currency, Landlord, Estate, House, Tenant,
TenantHouse, Employee, Supplier, BillingCycle, TaxType.

All core entities inherit from TimeStampedModel + SoftDeleteModel. History is
tracked via django-simple-history.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from .fields import UGXField, USDField


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)


class AllObjectsManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def soft_delete(self, user=None):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])


class CoreBaseModel(TimeStampedModel, SoftDeleteModel):
    class Meta:
        abstract = True


def compose_full_name(first_name, last_name, other_names=""):
    """Compose a canonical full_name from parts. Order: first, other, last."""
    parts = [
        (first_name or "").strip(),
        (other_names or "").strip(),
        (last_name or "").strip(),
    ]
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------
class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True)  # UGX, USD, ...
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8, blank=True)
    decimal_places = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code


# ---------------------------------------------------------------------------
# BillingCycle, TaxType (lookup/reference)
# ---------------------------------------------------------------------------
class BillingCycle(models.Model):
    class Unit(models.TextChoices):
        HOUR = "HOUR", "Hour"
        DAY = "DAY", "Day"
        WEEK = "WEEK", "Week"
        MONTH = "MONTH", "Month"
        QUARTER = "QUARTER", "Quarter"
        SEMI_ANNUAL = "SEMI_ANNUAL", "Semi-Annual"
        YEAR = "YEAR", "Year"

    name = models.CharField(max_length=64, unique=True)
    unit = models.CharField(max_length=16, choices=Unit.choices)
    count = models.PositiveIntegerField(default=1)  # e.g., 2 weeks = WEEK * 2
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["unit", "count"]

    def __str__(self):
        return self.name


class TaxType(models.Model):
    class Kind(models.TextChoices):
        VAT = "VAT", "VAT"
        WITHHOLDING = "WITHHOLDING", "Withholding Tax"
        OTHER = "OTHER", "Other"

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    rate = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0.000"))
    is_active = models.BooleanField(default=False)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.rate}%)"


# ---------------------------------------------------------------------------
# Landlord / Estate / House
# ---------------------------------------------------------------------------
class CommissionType(models.TextChoices):
    FIXED = "FIXED", "Fixed Amount"
    PERCENTAGE = "PERCENTAGE", "Percentage"


class CommissionScope(models.TextChoices):
    PER_HOUSE = "PER_HOUSE", "Per House"
    PER_ESTATE = "PER_ESTATE", "Per Estate"


class BillingMode(models.TextChoices):
    PREPAID = "PREPAID", "Prepaid"
    POSTPAID = "POSTPAID", "Postpaid"


class ProRataMode(models.TextChoices):
    PRO_RATA = "PRO_RATA", "Pro-Rata Billing"
    NEXT_CYCLE = "NEXT_CYCLE", "Next-Cycle Alignment"


class UtilityKind(models.TextChoices):
    """Utility line classification — drives income-account routing on
    separately-billed utility invoice lines. Must stay in sync with the
    `*_billed_separately` flags on SettingsMixin.
    """
    WATER = "WATER", "Water"
    GARBAGE = "GARBAGE", "Garbage / Waste"
    SECURITY = "SECURITY", "Security"
    ELECTRICITY = "ELECTRICITY", "Electricity"
    OTHER = "OTHER", "Other Utility"


# Map each utility kind to the `*_billed_separately` boolean on SettingsMixin
# and the accounting system-code for the matching income account.
UTILITY_FLAG_BY_KIND = {
    UtilityKind.WATER: "water_billed_separately",
    UtilityKind.GARBAGE: "garbage_billed_separately",
    UtilityKind.SECURITY: "security_billed_separately",
    UtilityKind.ELECTRICITY: "electricity_billed_separately",
    UtilityKind.OTHER: "other_bills_billed_separately",
}

UTILITY_INCOME_SYSCODE_BY_KIND = {
    UtilityKind.WATER: "WATER_INCOME",
    UtilityKind.GARBAGE: "GARBAGE_INCOME",
    UtilityKind.SECURITY: "SECURITY_INCOME",
    UtilityKind.ELECTRICITY: "ELECTRICITY_INCOME",
    UtilityKind.OTHER: "OTHER_UTILITY_INCOME",
}


class Landlord(CoreBaseModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    class StatementChannel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        BOTH = "BOTH", "Both"
        NONE = "NONE", "Do not auto-send"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="landlord_profile",
    )
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    other_names = models.CharField(max_length=120, blank=True)
    full_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Auto-composed from first/other/last on save. Do not edit directly.",
    )
    phone = models.CharField(max_length=16)
    email = models.EmailField(blank=True)
    id_number = models.CharField(max_length=64, blank=True)
    is_meili_owned = models.BooleanField(
        default=False,
        help_text="True for Meili-owned properties (no commission split).",
    )
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=64, blank=True)
    bank_branch = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    preferred_statement_channel = models.CharField(
        max_length=16,
        choices=StatementChannel.choices,
        default=StatementChannel.EMAIL,
        help_text="How to auto-deliver the monthly landlord statement PDF.",
    )
    whatsapp_number = models.CharField(
        max_length=16,
        blank=True,
        help_text="WhatsApp destination in E.164 if different from `phone`.",
    )
    notes = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["full_name"]

    def save(self, *args, **kwargs):
        composed = compose_full_name(self.first_name, self.last_name, self.other_names)
        if composed:
            self.full_name = composed
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse("core:landlord-detail", args=[self.pk])


class SettingsMixin(models.Model):
    """Shared settings fields between Estate and House.

    House values, when non-null, override Estate values via
    `get_effective_setting(house, field_name)`.
    """

    currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    billing_cycle = models.ForeignKey(
        BillingCycle, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    billing_mode = models.CharField(
        max_length=16, choices=BillingMode.choices, null=True, blank=True
    )
    prorata_mode = models.CharField(
        max_length=16, choices=ProRataMode.choices, null=True, blank=True
    )
    commission_type = models.CharField(
        max_length=16, choices=CommissionType.choices, null=True, blank=True
    )
    commission_scope = models.CharField(
        max_length=16, choices=CommissionScope.choices, null=True, blank=True
    )
    commission_amount = UGXField(null=True, blank=True)
    commission_percent = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True
    )
    tax_type = models.ForeignKey(
        TaxType, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    security_deposit_policy = models.CharField(max_length=120, blank=True)
    initial_deposit_policy = models.CharField(max_length=120, blank=True)
    account_manager = models.ForeignKey(
        "core.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    collections_person = models.ForeignKey(
        "core.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    water_billed_separately = models.BooleanField(null=True, blank=True)
    garbage_billed_separately = models.BooleanField(null=True, blank=True)
    security_billed_separately = models.BooleanField(null=True, blank=True)
    electricity_billed_separately = models.BooleanField(null=True, blank=True)
    other_bills_billed_separately = models.BooleanField(null=True, blank=True)
    other_bills_description = models.CharField(
        max_length=255,
        blank=True,
        help_text="Free-text description of what 'other bills' covers at this level.",
    )

    class Meta:
        abstract = True


class Estate(CoreBaseModel, SettingsMixin):
    landlord = models.ForeignKey(
        Landlord, on_delete=models.PROTECT, related_name="estates"
    )
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["landlord", "name"], name="uniq_estate_name_per_landlord"
            )
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("core:estate-detail", args=[self.pk])


class House(CoreBaseModel, SettingsMixin):
    class Occupancy(models.TextChoices):
        VACANT = "VACANT", "Vacant"
        OCCUPIED = "OCCUPIED", "Occupied"
        UNDER_MAINTENANCE = "UNDER_MAINTENANCE", "Under Maintenance"

    estate = models.ForeignKey(Estate, on_delete=models.PROTECT, related_name="houses")
    landlord = models.ForeignKey(
        Landlord, on_delete=models.PROTECT, null=True, blank=True, related_name="houses",
        help_text="Optional override — defaults to estate.landlord when null.",
    )
    house_number = models.CharField(max_length=32)
    name = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    periodic_rent = UGXField(null=True, blank=True)
    occupancy_status = models.CharField(
        max_length=24, choices=Occupancy.choices, default=Occupancy.VACANT
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["estate__name", "house_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["estate", "house_number"], name="uniq_house_number_per_estate"
            )
        ]

    def __str__(self):
        label = self.name or self.house_number
        return f"{label} ({self.estate.name})"

    def get_absolute_url(self):
        return reverse("core:house-detail", args=[self.pk])

    @property
    def effective_landlord(self):
        return self.landlord or self.estate.landlord


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------
class Employee(CoreBaseModel):
    class EmploymentType(models.TextChoices):
        FULL_TIME = "FULL_TIME", "Full-time"
        PART_TIME = "PART_TIME", "Part-time"
        CONTRACT = "CONTRACT", "Contract"
        INTERN = "INTERN", "Intern"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_profile"
    )
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    other_names = models.CharField(max_length=120, blank=True)
    full_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Auto-composed from first/other/last on save. Do not edit directly.",
    )
    phone = models.CharField(max_length=16, blank=True)
    id_number = models.CharField(max_length=64, blank=True)
    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    requires_checker = models.BooleanField(
        default=True,
        help_text="False = Trusted employee (can self-approve payments). Never applies to void/credit-note/refund.",
    )
    is_active = models.BooleanField(default=True)

    # --- Payroll / benefits (mini-payroll per SPEC §2A.5) --------------------
    job_title = models.CharField(max_length=120, blank=True)
    employment_type = models.CharField(
        max_length=16, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME
    )
    hire_date = models.DateField(null=True, blank=True)
    base_salary = UGXField(
        default=0, help_text="Gross monthly salary (UGX, whole shillings)."
    )
    allowance_transport = UGXField(default=0, help_text="Monthly transport allowance.")
    allowance_housing = UGXField(default=0, help_text="Monthly housing allowance.")
    allowance_airtime = UGXField(default=0, help_text="Monthly airtime allowance.")
    allowance_other = UGXField(default=0, help_text="Other monthly allowances.")
    paye_monthly = UGXField(
        default=0,
        help_text="PAYE withheld monthly (URA). Computed and snapshotted by payroll run.",
    )
    other_deduction = UGXField(default=0, help_text="Other monthly deductions.")
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=64, blank=True)
    bank_branch = models.CharField(max_length=120, blank=True)
    tin = models.CharField(
        max_length=32, blank=True, help_text="URA Tax Identification Number."
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["full_name"]

    def save(self, *args, **kwargs):
        composed = compose_full_name(self.first_name, self.last_name, self.other_names)
        if composed:
            self.full_name = composed
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse("core:employee-detail", args=[self.pk])

    @property
    def gross_monthly(self):
        return (
            self.base_salary
            + self.allowance_transport
            + self.allowance_housing
            + self.allowance_airtime
            + self.allowance_other
        )

    @property
    def net_monthly(self):
        return (
            self.gross_monthly
            - self.paye_monthly
            - self.other_deduction
        )

    @property
    def total_employer_cost(self):
        return self.gross_monthly


# ---------------------------------------------------------------------------
# Tenant
# ---------------------------------------------------------------------------
class Tenant(CoreBaseModel):
    class PreferredNotification(models.TextChoices):
        SMS = "SMS", "SMS"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        EMAIL = "EMAIL", "Email"

    class PreferredReceipt(models.TextChoices):
        WHATSAPP = "WHATSAPP", "WhatsApp"
        EMAIL = "EMAIL", "Email"
        WEB = "WEB", "Web Console"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_profile",
    )
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    other_names = models.CharField(max_length=120, blank=True)
    full_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Auto-composed from first/other/last on save. Do not edit directly.",
    )
    phone = models.CharField(max_length=16)
    email = models.EmailField(blank=True)
    id_number = models.CharField(max_length=64, blank=True)
    next_of_kin_name = models.CharField(max_length=200, blank=True)
    next_of_kin_phone = models.CharField(max_length=16, blank=True)
    preferred_notification = models.CharField(
        max_length=16,
        choices=PreferredNotification.choices,
        default=PreferredNotification.SMS,
    )
    preferred_receipt = models.CharField(
        max_length=16,
        choices=PreferredReceipt.choices,
        default=PreferredReceipt.EMAIL,
    )
    sales_rep = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="sold_tenants"
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["full_name"]

    def save(self, *args, **kwargs):
        composed = compose_full_name(self.first_name, self.last_name, self.other_names)
        if composed:
            self.full_name = composed
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse("core:tenant-detail", args=[self.pk])

    @property
    def derived_status(self):
        """Active / Prospect Only / Exited — derived from TenantHouse records."""
        statuses = set(self.tenancies.values_list("status", flat=True))
        if not statuses:
            return "Exited"
        if TenantHouse.Status.ACTIVE in statuses:
            return "Active"
        if statuses == {TenantHouse.Status.EXITED}:
            return "Exited"
        return "Prospect Only"


class TenantHouse(CoreBaseModel):
    class Status(models.TextChoices):
        PROSPECT = "PROSPECT", "Prospect"
        ACTIVE = "ACTIVE", "Active"
        EXITED = "EXITED", "Exited"

    class InvoiceGenerationStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        STOPPED = "STOPPED", "Stopped"

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="tenancies")
    house = models.ForeignKey(House, on_delete=models.PROTECT, related_name="tenancies")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROSPECT)
    invoice_generation_status = models.CharField(
        max_length=16,
        choices=InvoiceGenerationStatus.choices,
        default=InvoiceGenerationStatus.ACTIVE,
        help_text="PAUSED skips generation but keeps accrual; STOPPED halts billing entirely.",
    )
    invoice_generation_note = models.CharField(max_length=255, blank=True)
    move_in_date = models.DateField(null=True, blank=True)
    move_out_date = models.DateField(null=True, blank=True)
    billing_start_date = models.DateField(null=True, blank=True)
    security_deposit = UGXField(null=True, blank=True)
    initial_deposit = UGXField(null=True, blank=True)
    sales_rep = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    account_manager = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    collections_person = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "house"],
                condition=models.Q(is_deleted=False),
                name="uniq_active_tenant_house",
            )
        ]

    def __str__(self):
        return f"{self.tenant.full_name} @ {self.house} [{self.status}]"


# ---------------------------------------------------------------------------
# Supplier
# ---------------------------------------------------------------------------
class Supplier(CoreBaseModel):
    class Kind(models.TextChoices):
        GOODS = "GOODS", "Goods"
        SERVICES = "SERVICES", "Services"
        BOTH = "BOTH", "Both"

    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=16, blank=True)
    email = models.EmailField(blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.GOODS)
    tax_id = models.CharField(max_length=64, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("core:supplier-detail", args=[self.pk])
