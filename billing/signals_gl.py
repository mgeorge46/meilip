"""Auto-post GL journal entries for LandlordPayout and SupplierPayment when
they become effectively approved (APPROVED or AUTO_APPROVED). Idempotent: the
signal no-ops if `source_journal` is already set.

LandlordPayout:
    Dr  2100 Landlord Payable           (liability ↓)
    Cr  <bank.ledger_account>           (asset ↓)

SupplierPayment (routed to Maintenance & Repairs for now — per-category
routing can be added later once Supplier.category mapping lands):
    Dr  5100 Maintenance & Repairs      (expense ↑)
    Cr  <bank.ledger_account>           (asset ↓)
"""
from decimal import Decimal

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounting.models import Account, JournalEntry, JournalEntryLine
from accounting.utils import SYS_LANDLORD_PAYABLE, get_account

from .models import ApprovalStatus, ExpenseClaim, LandlordPayout, SupplierPayment
from .sequences import allocate_number


SYS_MAINTENANCE_REPAIRS = "MAINTENANCE_REPAIRS"


def _is_approved(obj):
    return obj.approval_status in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED)


def _create_and_post_journal(*, memo, source, lines, entry_date, user=None):
    """Create a balanced JournalEntry with the given (account, debit, credit)
    tuples and post it atomically."""
    with transaction.atomic():
        entry = JournalEntry.objects.create(
            reference=allocate_number("JE"),
            entry_date=entry_date,
            memo=memo,
            source=source,
            created_by=user,
        )
        for account, debit, credit in lines:
            JournalEntryLine.objects.create(
                entry=entry, account=account,
                debit=debit or Decimal("0"),
                credit=credit or Decimal("0"),
            )
        entry.post(user=user)
    return entry


@receiver(post_save, sender=LandlordPayout)
def post_landlord_payout_journal(sender, instance, created, **kwargs):
    if not _is_approved(instance):
        return
    if instance.source_journal_id:
        return
    if not instance.bank_account or not instance.bank_account.ledger_account_id:
        return

    try:
        landlord_payable = get_account(SYS_LANDLORD_PAYABLE)
    except Account.DoesNotExist:
        return

    bank_ledger = instance.bank_account.ledger_account
    entry = _create_and_post_journal(
        memo=f"Landlord payout {instance.number} to {instance.landlord.full_name}",
        source=JournalEntry.Source.MANUAL,
        lines=[
            (landlord_payable, instance.amount, None),
            (bank_ledger, None, instance.amount),
        ],
        entry_date=instance.paid_at.date() if instance.paid_at else None,
        user=instance.checker or instance.maker,
    )
    # Attach without re-triggering the signal
    LandlordPayout.objects.filter(pk=instance.pk).update(source_journal=entry)


@receiver(post_save, sender=SupplierPayment)
def post_supplier_payment_journal(sender, instance, created, **kwargs):
    if not _is_approved(instance):
        return
    if instance.source_journal_id:
        return
    if not instance.bank_account or not instance.bank_account.ledger_account_id:
        return

    try:
        expense_account = get_account(SYS_MAINTENANCE_REPAIRS)
    except Account.DoesNotExist:
        return

    bank_ledger = instance.bank_account.ledger_account
    entry = _create_and_post_journal(
        memo=f"Supplier payment {instance.number} to {instance.supplier.name} — {instance.service_description[:80]}",
        source=JournalEntry.Source.MANUAL,
        lines=[
            (expense_account, instance.amount, None),
            (bank_ledger, None, instance.amount),
        ],
        entry_date=instance.paid_at.date() if instance.paid_at else None,
        user=instance.checker or instance.maker,
    )
    SupplierPayment.objects.filter(pk=instance.pk).update(source_journal=entry)


@receiver(post_save, sender=ExpenseClaim)
def post_expense_claim_journal(sender, instance, created, **kwargs):
    if not _is_approved(instance):
        return
    if instance.source_journal_id:
        return
    if not instance.reimbursement_bank or not instance.reimbursement_bank.ledger_account_id:
        return

    # Map category → expense account; fallback to MAINTENANCE_REPAIRS if the
    # specific expense account isn't seeded yet.
    sys_code = ExpenseClaim.CATEGORY_TO_SYSTEM_CODE.get(
        instance.category, SYS_MAINTENANCE_REPAIRS,
    )
    try:
        expense_account = get_account(sys_code)
    except Account.DoesNotExist:
        try:
            expense_account = get_account(SYS_MAINTENANCE_REPAIRS)
        except Account.DoesNotExist:
            return  # no fallback available — skip posting

    bank_ledger = instance.reimbursement_bank.ledger_account
    entry = _create_and_post_journal(
        memo=f"Expense claim {instance.number} — {instance.claimant.full_name} — {instance.description[:80]}",
        source=JournalEntry.Source.MANUAL,
        lines=[
            (expense_account, instance.amount, None),
            (bank_ledger, None, instance.amount),
        ],
        entry_date=instance.incurred_at,
        user=instance.checker or instance.maker,
    )
    ExpenseClaim.objects.filter(pk=instance.pk).update(source_journal=entry)
