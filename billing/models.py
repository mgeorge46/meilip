"""Billing models — Invoice, InvoiceLine, InvoiceTaxLine, AdHocCharge,
Payment, PaymentAllocation, Receipt, InvoiceVoid, CreditNote, Refund.

All money is stored in UGX via `UGXField` per SPEC §14. State transitions
and delete-protection are enforced at the model layer so ORM/admin/URL
bypasses cannot corrupt the ledger.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from simple_history.models import HistoricalRecords

from core.fields import UGXField
from core.models import CoreBaseModel, TenantHouse

from .exceptions import (
    CreditNoteExceedsInvoice,
    InvalidInvoiceTransition,
    ProtectedFinancialRecord,
    SelfApprovalBlocked,
    TrustedBypassBlocked,
)
from .sequences import NumberSequence, allocate_number  # noqa: F401 — re-exported


# ---------------------------------------------------------------------------
# Approval mixin (maker-checker)
# ---------------------------------------------------------------------------
class ApprovalStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    AUTO_APPROVED = "AUTO_APPROVED", "Auto-approved (trusted)"


class MakerCheckerMixin(models.Model):
    """Mixin for records requiring maker-checker.

    `allow_trusted_bypass` — overridden by subclass; True only on payments
    and ad-hoc charges per CLAUDE.md. Voids/credit notes/refunds always
    return False.
    """
    approval_status = models.CharField(
        max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING
    )
    maker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    checker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    allow_trusted_bypass = False  # subclasses override

    class Meta:
        abstract = True

    # --- Approval API -------------------------------------------------------
    def approve(self, user):
        if self.approval_status != ApprovalStatus.PENDING:
            raise ValidationError("Only pending records can be approved.")
        if self.maker_id and self.maker_id == user.id:
            raise SelfApprovalBlocked("A maker cannot approve their own submission.")
        self.checker = user
        self.approval_status = ApprovalStatus.APPROVED
        self.approved_at = timezone.now()
        self.save(update_fields=[
            "checker", "approval_status", "approved_at"
        ])

    def reject(self, user, reason):
        if self.approval_status != ApprovalStatus.PENDING:
            raise ValidationError("Only pending records can be rejected.")
        if self.maker_id and self.maker_id == user.id:
            raise SelfApprovalBlocked("A maker cannot reject their own submission.")
        self.checker = user
        self.approval_status = ApprovalStatus.REJECTED
        self.rejection_reason = reason or ""
        self.approved_at = timezone.now()
        self.save(update_fields=[
            "checker", "approval_status", "rejection_reason", "approved_at"
        ])

    def try_trusted_autoapprove(self):
        """If `allow_trusted_bypass` is True and the maker's Employee record
        has `requires_checker=False`, promote to AUTO_APPROVED atomically."""
        if not self.allow_trusted_bypass:
            raise TrustedBypassBlocked("This record type cannot be auto-approved.")
        if not self.maker_id:
            return False
        try:
            emp = self.maker.employee_profile
        except Exception:
            return False
        if emp.requires_checker:
            return False
        self.approval_status = ApprovalStatus.AUTO_APPROVED
        self.approved_at = timezone.now()
        self.save(update_fields=["approval_status", "approved_at"])
        return True

    @property
    def is_effectively_approved(self):
        return self.approval_status in (
            ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED
        )


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
class Invoice(CoreBaseModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        PAID = "PAID", "Paid"
        OVERDUE = "OVERDUE", "Overdue"
        VOIDED = "VOIDED", "Voided"
        CANCELLED = "CANCELLED", "Cancelled"

    _ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.ISSUED, Status.CANCELLED},
        Status.ISSUED: {Status.PARTIALLY_PAID, Status.PAID, Status.OVERDUE, Status.VOIDED},
        Status.PARTIALLY_PAID: {Status.PAID, Status.OVERDUE, Status.VOIDED},
        Status.PAID: {Status.VOIDED},  # void-after-paid allowed per SPEC
        Status.OVERDUE: {Status.PARTIALLY_PAID, Status.PAID, Status.VOIDED},
        Status.VOIDED: set(),
        Status.CANCELLED: set(),
    }

    DELETABLE_STATUSES = {Status.DRAFT, Status.CANCELLED}

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    tenant_house = models.ForeignKey(
        TenantHouse, on_delete=models.PROTECT, related_name="invoices"
    )
    period_from = models.DateField()
    period_to = models.DateField()
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()

    rent_amount = UGXField(default=Decimal("0"))
    subtotal = UGXField(default=Decimal("0"))  # sum of lines (rent + ad-hocs on invoice)
    tax_total = UGXField(default=Decimal("0"))
    total = UGXField(default=Decimal("0"))  # subtotal + tax_total

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    is_backdated = models.BooleanField(default=False)
    backdate_reason = models.TextField(blank=True)

    notes = models.TextField(blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)

    source_journal = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_invoices",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["tenant_house", "period_from", "period_to"]),
        ]

    def __str__(self):
        return self.number or f"INV(draft#{self.pk})"

    # --- State machine ------------------------------------------------------
    def transition_to(self, new_status, *, user=None, save=True):
        cur = self.Status(self.status)
        new = self.Status(new_status)
        if new not in self._ALLOWED_TRANSITIONS[cur]:
            raise InvalidInvoiceTransition(
                f"Illegal transition {cur} -> {new}"
            )
        self.status = new
        if new == self.Status.ISSUED and not self.issued_at:
            self.issued_at = timezone.now()
            if not self.number:
                self.number = allocate_number("INV")
        if save:
            self.save()

    # --- Derived amounts ----------------------------------------------------
    @property
    def amount_paid(self):
        return (
            self.allocations.filter(payment__approval_status__in=[
                ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED
            ]).aggregate(s=models.Sum("amount"))["s"]
            or Decimal("0")
        )

    @property
    def credits_applied(self):
        return (
            self.credit_notes.filter(approval_status__in=[
                ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED
            ]).aggregate(s=models.Sum("amount"))["s"]
            or Decimal("0")
        )

    @property
    def outstanding(self):
        return (self.total or Decimal("0")) - self.amount_paid - self.credits_applied

    # --- Delete guard -------------------------------------------------------
    def delete(self, *args, **kwargs):
        if self.status not in self.DELETABLE_STATUSES:
            raise ProtectedFinancialRecord(
                f"Invoice {self.number or self.pk} is {self.get_status_display()} "
                "— only Draft or Cancelled invoices can be deleted."
            )
        return super().delete(*args, **kwargs)

    def recalculate_totals(self):
        lines = list(self.lines.all())
        tax_lines = list(self.tax_lines.all())
        subtotal = sum((l.amount for l in lines), Decimal("0")) or self.rent_amount or Decimal("0")
        tax_total = sum((t.amount for t in tax_lines), Decimal("0"))
        self.subtotal = subtotal
        self.tax_total = tax_total
        self.total = subtotal + tax_total


class InvoiceLine(CoreBaseModel):
    class Kind(models.TextChoices):
        RENT = "RENT", "Rent"
        AD_HOC = "AD_HOC", "Ad-hoc"
        PRORATA = "PRORATA", "Pro-rata rent"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    description = models.CharField(max_length=255)
    amount = UGXField()
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)

    # For ad-hoc lines we track who is billed (tenant pays, but income
    # accrues to landlord or Meili).
    TARGET_LANDLORD = "LANDLORD"
    TARGET_MEILI = "MEILI"
    TARGETS = [(TARGET_LANDLORD, "Landlord"), (TARGET_MEILI, "Meili")]
    target = models.CharField(max_length=16, choices=TARGETS, blank=True)

    class Meta:
        ordering = ["id"]


class InvoiceTaxLine(CoreBaseModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="tax_lines")
    tax_type = models.ForeignKey(
        "core.TaxType", on_delete=models.PROTECT, related_name="+"
    )
    taxable_amount = UGXField()
    rate_percent = models.DecimalField(max_digits=6, decimal_places=3)
    amount = UGXField()

    class Meta:
        ordering = ["id"]


# ---------------------------------------------------------------------------
# Ad-hoc Charge (scheduled / on-demand, can be attached to an invoice or
# stand alone to be picked up on next invoice generation)
# ---------------------------------------------------------------------------
class AdHocCharge(MakerCheckerMixin, CoreBaseModel):
    allow_trusted_bypass = True  # ad-hoc charges may be trusted-bypassed

    class Target(models.TextChoices):
        LANDLORD = "LANDLORD", "Landlord"
        MEILI = "MEILI", "Meili"

    tenant_house = models.ForeignKey(
        TenantHouse, on_delete=models.PROTECT, related_name="ad_hoc_charges"
    )
    description = models.CharField(max_length=255)
    amount = UGXField()
    target = models.CharField(max_length=16, choices=Target.choices)
    bill_on_or_after = models.DateField(default=timezone.localdate)
    attached_invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ad_hoc_charges",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# Payment + PaymentAllocation (FIFO)
# ---------------------------------------------------------------------------
class Payment(MakerCheckerMixin, CoreBaseModel):
    allow_trusted_bypass = True

    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
        OTHER = "OTHER", "Other"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.PROTECT, related_name="payments"
    )
    amount = UGXField()
    method = models.CharField(max_length=16, choices=Method.choices)
    bank_account = models.ForeignKey(
        "accounting.BankAccount", on_delete=models.PROTECT, related_name="payments"
    )
    reference_number = models.CharField(max_length=64, blank=True)
    received_at = models.DateTimeField(default=timezone.now)

    source_journal = models.ForeignKey(
        "accounting.JournalEntry", on_delete=models.PROTECT, null=True, blank=True,
        related_name="source_payments",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return self.number or f"PMT(pending#{self.pk})"


class PaymentAllocation(models.Model):
    """Ties an approved payment's currency to an invoice for FIFO application.
    Also used for advances that are later applied to future invoices.
    """
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, null=True, blank=True,
        related_name="allocations",
    )
    # If no invoice is set, this row represents a deferred advance sitting on
    # the held-advance liability account.
    amount = UGXField()
    allocated_at = models.DateTimeField(default=timezone.now)
    is_advance_hold = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["allocated_at", "id"]


# ---------------------------------------------------------------------------
# Receipt + RefundReceipt
# ---------------------------------------------------------------------------
class Receipt(CoreBaseModel):
    class Kind(models.TextChoices):
        PAYMENT = "PAYMENT", "Payment"
        REFUND = "REFUND", "Refund"

    number = models.CharField(max_length=32, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.PAYMENT)
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, null=True, blank=True, related_name="receipts"
    )
    refund = models.ForeignKey(
        "billing.Refund", on_delete=models.PROTECT, null=True, blank=True,
        related_name="receipts",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    amount = UGXField()

    history = HistoricalRecords()

    class Meta:
        ordering = ["-issued_at", "-id"]

    def __str__(self):
        return self.number


# ---------------------------------------------------------------------------
# Invoice Void
# ---------------------------------------------------------------------------
class InvoiceVoid(MakerCheckerMixin, CoreBaseModel):
    allow_trusted_bypass = False  # spec: never

    class Reason(models.TextChoices):
        DATA_ENTRY_ERROR = "DATA_ENTRY_ERROR", "Data entry error"
        TENANT_DISPUTE = "TENANT_DISPUTE", "Tenant dispute resolved in tenant's favour"
        CANCELLATION = "CANCELLATION", "Lease cancellation"
        OTHER = "OTHER", "Other"

    invoice = models.OneToOneField(
        Invoice, on_delete=models.PROTECT, related_name="void_record"
    )
    reason_category = models.CharField(max_length=32, choices=Reason.choices)
    reason = models.TextField()
    void_date = models.DateField(default=timezone.localdate)
    reversing_journal = models.ForeignKey(
        "accounting.JournalEntry", on_delete=models.PROTECT, null=True, blank=True,
        related_name="source_invoice_voids",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# Credit Note
# ---------------------------------------------------------------------------
class CreditNote(MakerCheckerMixin, CoreBaseModel):
    allow_trusted_bypass = False

    class Reason(models.TextChoices):
        OVERCHARGE = "OVERCHARGE", "Overcharge correction"
        GOODWILL = "GOODWILL", "Goodwill / dispute adjustment"
        SERVICE_FAILURE = "SERVICE_FAILURE", "Service failure credit"
        OTHER = "OTHER", "Other"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    original_invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="credit_notes"
    )
    amount = UGXField()
    reason_category = models.CharField(max_length=32, choices=Reason.choices)
    reason = models.TextField()
    source_journal = models.ForeignKey(
        "accounting.JournalEntry", on_delete=models.PROTECT, null=True, blank=True,
        related_name="source_credit_notes",
    )
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= 0:
            raise ValidationError({"amount": "Credit note amount must be positive."})
        # Cap at original invoice outstanding + existing non-voided credit
        if self.original_invoice_id:
            applied = (
                CreditNote.objects.filter(
                    original_invoice=self.original_invoice, is_voided=False
                )
                .exclude(pk=self.pk)
                .aggregate(s=models.Sum("amount"))["s"]
                or Decimal("0")
            )
            max_creditable = self.original_invoice.total - applied
            if self.amount > max_creditable:
                raise CreditNoteExceedsInvoice(
                    f"Credit note {self.amount} exceeds invoice {self.original_invoice} "
                    f"remaining capacity {max_creditable}"
                )


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------
class Refund(MakerCheckerMixin, CoreBaseModel):
    allow_trusted_bypass = False

    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank transfer"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile money"
        CHEQUE = "CHEQUE", "Cheque"

    class Source(models.TextChoices):
        HELD_ADVANCE = "HELD_ADVANCE", "Tenant advance held"
        SECURITY_DEPOSIT = "SECURITY_DEPOSIT", "Security deposit refundable"
        AR_CREDIT = "AR_CREDIT", "AR credit balance"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="refunds")
    tenant_house = models.ForeignKey(
        TenantHouse, on_delete=models.PROTECT, null=True, blank=True,
        related_name="refunds",
    )
    amount = UGXField()
    method = models.CharField(max_length=16, choices=Method.choices)
    source = models.CharField(max_length=32, choices=Source.choices)
    source_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="refunds_from",
    )
    bank_account = models.ForeignKey(
        "accounting.BankAccount", on_delete=models.PROTECT,
        related_name="refunds_paid_from", null=True, blank=True,
    )
    destination_details = models.CharField(max_length=255)
    reference_number = models.CharField(max_length=64)
    linked_credit_note = models.ForeignKey(
        CreditNote, on_delete=models.PROTECT, null=True, blank=True,
        related_name="linked_refunds",
    )
    reason = models.TextField()

    refund_journal = models.ForeignKey(
        "accounting.JournalEntry", on_delete=models.PROTECT, null=True, blank=True,
        related_name="source_refunds",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# Commission posting record — links a posted commission journal back to the
# invoice & period that recognised it, so we can reverse proportionally on
# voids / credit notes.
# ---------------------------------------------------------------------------
class CommissionPosting(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="commission_postings"
    )
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry", on_delete=models.PROTECT, related_name="+"
    )
    amount = UGXField()
    posted_at = models.DateTimeField(default=timezone.now)
    is_reversal = models.BooleanField(default=False)
    reverses = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by"
    )

    class Meta:
        ordering = ["posted_at", "id"]
