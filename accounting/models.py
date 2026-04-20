"""Chart of Accounts & double-entry journal.

All monetary amounts are stored in UGX (whole shillings) for ledger integrity.
USD-denominated source transactions are converted to UGX at posting time via
the BankAccount/Account-level currency reference. See SPEC §14.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from simple_history.models import HistoricalRecords

from core.fields import UGXField
from core.models import CoreBaseModel


# ---------------------------------------------------------------------------
# AccountType — Asset / Liability / Equity / Revenue / Expense
# ---------------------------------------------------------------------------
class AccountType(models.Model):
    class Category(models.TextChoices):
        ASSET = "ASSET", "Asset"
        LIABILITY = "LIABILITY", "Liability"
        EQUITY = "EQUITY", "Equity"
        REVENUE = "REVENUE", "Revenue"
        EXPENSE = "EXPENSE", "Expense"

    class Normal(models.TextChoices):
        DEBIT = "DEBIT", "Debit"
        CREDIT = "CREDIT", "Credit"

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    category = models.CharField(max_length=16, choices=Category.choices)
    normal_balance = models.CharField(max_length=8, choices=Normal.choices)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordering", "category", "code"]

    def __str__(self):
        return self.name


class Account(CoreBaseModel):
    """A ledger account.

    Accounts form a hierarchy — a parent account groups children, and posting
    is only allowed on leaf accounts. Each account has a system code used by
    accounting utilities (e.g., `TENANT_ADVANCE_HELD_MANAGED`).
    """

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=120)
    account_type = models.ForeignKey(
        AccountType, on_delete=models.PROTECT, related_name="accounts"
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    system_code = models.CharField(
        max_length=64,
        blank=True,
        unique=True,
        null=True,
        help_text="Stable identifier used by code (e.g., 'TENANT_ADVANCE_HELD_MANAGED').",
    )
    description = models.CharField(max_length=255, blank=True)
    currency = models.ForeignKey(
        "core.Currency",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Display currency. Ledger totals are always UGX.",
    )
    is_postable = models.BooleanField(
        default=True,
        help_text="False for parent/rollup accounts — posting only on leaves.",
    )
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def category(self):
        return self.account_type.category

    @property
    def normal_balance(self):
        return self.account_type.normal_balance

    def clean(self):
        super().clean()
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError("An account cannot be its own parent.")
        if self.parent and self.parent.is_postable:
            raise ValidationError(
                "Parent account must be non-postable — flag it `is_postable=False`."
            )

    def balance(self, *, as_of=None):
        """Return the UGX balance of this account.

        For debit-normal accounts: debits − credits. For credit-normal
        accounts: credits − debits. So a positive return value always
        represents a "healthy" balance in the account's natural direction.
        """
        # REVERSED entries kept their original postings on the ledger —
        # their offsetting reversal is itself a POSTED entry. Both are included.
        qs = JournalEntryLine.objects.filter(
            account=self,
            entry__status__in=[JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED],
        )
        if as_of is not None:
            qs = qs.filter(entry__posted_at__lte=as_of)
        agg = qs.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit"),
        )
        d = agg["debit"] or Decimal("0")
        c = agg["credit"] or Decimal("0")
        if self.normal_balance == AccountType.Normal.DEBIT:
            return d - c
        return c - d


# ---------------------------------------------------------------------------
# JournalEntry + JournalEntryLine
# ---------------------------------------------------------------------------
class JournalEntry(CoreBaseModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        REVERSED = "REVERSED", "Reversed"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        INVOICE = "INVOICE", "Invoice"
        PAYMENT = "PAYMENT", "Payment"
        VOID = "VOID", "Void"
        CREDIT_NOTE = "CREDIT_NOTE", "Credit Note"
        REFUND = "REFUND", "Refund"
        COMMISSION = "COMMISSION", "Commission"
        SYSTEM = "SYSTEM", "System"

    reference = models.CharField(max_length=48, unique=True, null=True, blank=True)
    entry_date = models.DateField(default=timezone.localdate)
    memo = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_journal_entries",
    )
    reverses = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_by",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-entry_date", "-id"]
        indexes = [
            models.Index(fields=["status", "entry_date"]),
            models.Index(fields=["source"]),
        ]

    def __str__(self):
        return f"{self.reference or f'JE-{self.pk}'} ({self.status})"

    # ---- helpers --------------------------------------------------------
    def totals(self):
        agg = self.lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        return agg["d"] or Decimal("0"), agg["c"] or Decimal("0")

    def is_balanced(self):
        d, c = self.totals()
        return d == c and d > 0

    # ---- state transitions ---------------------------------------------
    @transaction.atomic
    def post(self, user=None):
        if self.status == self.Status.POSTED:
            raise ValidationError("Journal entry already posted.")
        if self.status == self.Status.REVERSED:
            raise ValidationError("Reversed entries cannot be re-posted.")
        lines = list(self.lines.select_related("account", "account__account_type"))
        if not lines:
            raise ValidationError("Journal entry has no lines.")
        for line in lines:
            if line.account.is_postable is False:
                raise ValidationError(
                    f"Cannot post to non-postable account {line.account.code}."
                )
            if not line.account.is_active:
                raise ValidationError(
                    f"Account {line.account.code} is inactive."
                )
        if not self.is_balanced():
            d, c = self.totals()
            raise ValidationError(
                f"Journal entry not balanced: debits={d}, credits={c}."
            )
        self.status = self.Status.POSTED
        self.posted_at = timezone.now()
        self.posted_by = user
        if not self.reference:
            self.reference = self._generate_reference()
        self.save(update_fields=["status", "posted_at", "posted_by", "reference", "updated_at"])
        return self

    @transaction.atomic
    def reverse(self, user=None, memo=None):
        if self.status != self.Status.POSTED:
            raise ValidationError("Only posted entries can be reversed.")
        if hasattr(self, "reversed_by"):
            raise ValidationError("This entry has already been reversed.")
        reversal = JournalEntry.objects.create(
            entry_date=timezone.localdate(),
            memo=memo or f"Reversal of {self.reference}",
            source=self.Source.SYSTEM,
            reverses=self,
            created_by=user,
        )
        for line in self.lines.all():
            JournalEntryLine.objects.create(
                entry=reversal,
                account=line.account,
                debit=line.credit,
                credit=line.debit,
                description=f"Reversal: {line.description}" if line.description else "",
            )
        reversal.post(user=user)
        self.status = self.Status.REVERSED
        self.save(update_fields=["status", "updated_at"])
        return reversal

    def _generate_reference(self):
        return f"JE-{self.entry_date:%Y%m}-{self.pk:06d}"


class JournalEntryLine(models.Model):
    entry = models.ForeignKey(
        JournalEntry, on_delete=models.CASCADE, related_name="lines"
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = UGXField(default=Decimal("0"))
    credit = UGXField(default=Decimal("0"))
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gte=0) & models.Q(credit__gte=0),
                name="jel_amounts_non_negative",
            ),
            models.CheckConstraint(
                condition=~(models.Q(debit__gt=0) & models.Q(credit__gt=0)),
                name="jel_not_both_sides",
            ),
            models.CheckConstraint(
                condition=models.Q(debit__gt=0) | models.Q(credit__gt=0),
                name="jel_at_least_one_side",
            ),
        ]

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.account.code} {side}"

    def clean(self):
        super().clean()
        if self.debit and self.credit:
            raise ValidationError("A line cannot have both debit and credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A line must have either a debit or credit amount.")


# ---------------------------------------------------------------------------
# BankAccount — cash / bank / mobile money — links to an Account for posting
# ---------------------------------------------------------------------------
class BankAccount(CoreBaseModel):
    class Kind(models.TextChoices):
        BANK = "BANK", "Bank"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
        CASH = "CASH", "Cash"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    bank_name = models.CharField(max_length=120, blank=True)
    account_number = models.CharField(max_length=64, blank=True)
    branch = models.CharField(max_length=120, blank=True)
    mobile_provider = models.CharField(max_length=64, blank=True)
    mobile_number = models.CharField(max_length=32, blank=True)
    currency = models.ForeignKey(
        "core.Currency", on_delete=models.PROTECT, related_name="+"
    )
    ledger_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="bank_accounts",
        help_text="Chart of Accounts entry this payment account posts to.",
    )
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.name}"
