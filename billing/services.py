"""Billing services — invoice generation, FIFO allocation, commission,
void / credit-note / refund workflows.

All posting happens through balanced JournalEntry instances so the ledger
stays correct; numeric primitives are `Decimal` to avoid float drift. UGX
is stored as whole shillings via UGXField per SPEC §14.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import JournalEntry, JournalEntryLine
from accounting.utils import (
    SYS_AR_TENANTS,
    SYS_COMMISSION_INCOME,
    SYS_LANDLORD_PAYABLE,
    SYS_RENT_INCOME,
    SYS_TAX_PAYABLE,
    get_account,
    get_advance_holding_account,
)
from core.models import (
    BillingCycle,
    BillingMode,
    ProRataMode,
    UTILITY_FLAG_BY_KIND,
    UTILITY_INCOME_SYSCODE_BY_KIND,
)
from core.utils import get_effective_setting

from .exceptions import (
    CreditNoteExceedsInvoice,
    InvoiceGenerationPaused,
    SelfApprovalBlocked,
    TrustedBypassBlocked,
)
from .models import (
    AdHocCharge,
    ApprovalStatus,
    CommissionPosting,
    CreditNote,
    Invoice,
    InvoiceLine,
    InvoiceTaxLine,
    InvoiceVoid,
    Payment,
    PaymentAllocation,
    Receipt,
    Refund,
)
from .sequences import allocate_number


# ---------------------------------------------------------------------------
# Period arithmetic
# ---------------------------------------------------------------------------
def _add_cycle(d: date, cycle: BillingCycle) -> date:
    """Return the date `cycle` after `d`."""
    unit = cycle.unit
    n = cycle.count or 1
    if unit == BillingCycle.Unit.DAY:
        return d + timedelta(days=n)
    if unit == BillingCycle.Unit.WEEK:
        return d + timedelta(weeks=n)
    if unit == BillingCycle.Unit.HOUR:
        return d + timedelta(hours=n)
    # Month-based — add months manually, clamp day.
    months = n if unit == BillingCycle.Unit.MONTH else (
        3 * n if unit == BillingCycle.Unit.QUARTER else
        6 * n if unit == BillingCycle.Unit.SEMI_ANNUAL else
        12 * n
    )
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    from calendar import monthrange
    day = min(d.day, monthrange(y, m)[1])
    return date(y, m, day)


def compute_next_period(tenancy, cycle, *, today):
    """Return (period_from, period_to) for the NEXT invoice for this tenancy.

    If no invoices exist yet, anchor on `billing_start_date` (or move_in_date).
    Otherwise anchor on the latest invoice's period_to + 1 day.
    """
    latest = (
        tenancy.invoices.exclude(status__in=[Invoice.Status.VOIDED, Invoice.Status.CANCELLED])
        .order_by("-period_to").first()
    )
    if latest is not None:
        start = latest.period_to + timedelta(days=1)
    else:
        start = tenancy.billing_start_date or tenancy.move_in_date or today
    end = _add_cycle(start, cycle) - timedelta(days=1)
    return start, end


def compute_prorata(tenancy, cycle, *, period_rent, today):
    """If this is the first invoice and tenant moved in mid-cycle, compute
    pro-rata adjustments depending on the house's prorata_mode.

    Returns: list[tuple[kind, description, amount, period_from, period_to]]
    """
    lines = []
    prorata_mode = get_effective_setting(tenancy.house, "prorata_mode") or ProRataMode.PRO_RATA
    has_prior = tenancy.invoices.exclude(
        status__in=[Invoice.Status.VOIDED, Invoice.Status.CANCELLED]
    ).exists()
    if has_prior or prorata_mode == ProRataMode.NEXT_CYCLE:
        return lines
    start = tenancy.billing_start_date or tenancy.move_in_date
    if not start:
        return lines
    # Pro-rata full cycle anchored on start_of_month / cycle boundary — the
    # simplest model: charge (remaining_days_of_cycle / total_days) * rent for
    # the partial first cycle.
    cycle_start = date(start.year, start.month, 1)
    cycle_end = _add_cycle(cycle_start, cycle) - timedelta(days=1)
    total_days = (cycle_end - cycle_start).days + 1
    remaining = (cycle_end - start).days + 1
    if remaining >= total_days or remaining <= 0:
        return lines
    prorata_amount = (Decimal(period_rent) * Decimal(remaining) / Decimal(total_days)).quantize(Decimal("1"))
    lines.append(
        (
            InvoiceLine.Kind.PRORATA,
            f"Pro-rata rent {start:%Y-%m-%d} to {cycle_end:%Y-%m-%d} ({remaining}/{total_days} days)",
            prorata_amount,
            start,
            cycle_end,
        )
    )
    return lines


# ---------------------------------------------------------------------------
# Invoice generation
# ---------------------------------------------------------------------------
@dataclass
class InvoiceDraft:
    invoice: Invoice
    created: bool
    posted: bool


def _is_due_for_generation(tenancy, cycle, *, today):
    """Decide whether this tenancy's next invoice should be generated now."""
    start, end = compute_next_period(tenancy, cycle, today=today)
    mode = get_effective_setting(tenancy.house, "billing_mode") or BillingMode.PREPAID
    # PREPAID: generate on/after period_from (bill in advance of period)
    # POSTPAID: generate on/after period_to + 1 day
    threshold = start if mode == BillingMode.PREPAID else end + timedelta(days=1)
    return today >= threshold, start, end


@transaction.atomic
def generate_invoice_for_tenancy(tenancy, *, user=None, today=None, force=False):
    """Generate the next invoice for one tenancy, issue it, and post its
    accrual journal. Idempotent — if an invoice already covers the next
    period, returns None.

    Raises `InvoiceGenerationPaused` if the tenancy is paused/stopped.
    """
    today = today or timezone.localdate()
    if tenancy.invoice_generation_status != tenancy.InvoiceGenerationStatus.ACTIVE:
        raise InvoiceGenerationPaused(
            f"Tenancy {tenancy.pk} is {tenancy.invoice_generation_status}"
        )
    cycle = get_effective_setting(tenancy.house, "billing_cycle")
    if cycle is None:
        raise ValidationError(
            f"Tenancy {tenancy.pk}: no billing cycle set on house or estate."
        )
    due, start, end = _is_due_for_generation(tenancy, cycle, today=today)
    if not (due or force):
        return None

    # Idempotency: if an invoice already covers this period, skip.
    existing = tenancy.invoices.filter(period_from=start, period_to=end).first()
    if existing is not None:
        return InvoiceDraft(invoice=existing, created=False, posted=existing.status != Invoice.Status.DRAFT)

    rent = tenancy.house.periodic_rent or Decimal("0")
    if rent <= 0:
        raise ValidationError(
            f"Tenancy {tenancy.pk}: house has no periodic_rent set."
        )

    due_days = 14  # could become a setting later
    invoice = Invoice.objects.create(
        tenant_house=tenancy,
        period_from=start,
        period_to=end,
        issue_date=today,
        due_date=start + timedelta(days=due_days),
        rent_amount=rent,
        created_by=user,
    )

    # Lines: rent + pro-rata (first invoice only) + pending ad-hoc charges
    InvoiceLine.objects.create(
        invoice=invoice,
        kind=InvoiceLine.Kind.RENT,
        description=f"Rent {start:%Y-%m-%d} to {end:%Y-%m-%d}",
        amount=rent,
        period_from=start,
        period_to=end,
        target=InvoiceLine.TARGET_LANDLORD,
    )
    for (kind, desc, amt, pf, pt) in compute_prorata(tenancy, cycle, period_rent=rent, today=today):
        InvoiceLine.objects.create(
            invoice=invoice,
            kind=kind,
            description=desc,
            amount=amt,
            period_from=pf,
            period_to=pt,
            target=InvoiceLine.TARGET_LANDLORD,
        )

    pending_adhoc = AdHocCharge.objects.filter(
        tenant_house=tenancy,
        attached_invoice__isnull=True,
        bill_on_or_after__lte=today,
        approval_status__in=[ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED],
    )
    for charge in pending_adhoc:
        target = (
            InvoiceLine.TARGET_MEILI if charge.target == AdHocCharge.Target.MEILI
            else InvoiceLine.TARGET_LANDLORD
        )
        # A utility charge becomes a UTILITY line only if the house/estate
        # flag for that utility is True (separately billed). Otherwise it
        # falls back to a regular AD_HOC line (bundled accounting).
        if charge.utility_kind:
            flag_name = UTILITY_FLAG_BY_KIND.get(charge.utility_kind)
            flag_on = bool(flag_name and get_effective_setting(tenancy.house, flag_name))
            if flag_on:
                InvoiceLine.objects.create(
                    invoice=invoice,
                    kind=InvoiceLine.Kind.UTILITY,
                    description=charge.description,
                    amount=charge.amount,
                    target=target,
                    utility_kind=charge.utility_kind,
                )
                charge.attached_invoice = invoice
                charge.save(update_fields=["attached_invoice", "updated_at"])
                continue
        InvoiceLine.objects.create(
            invoice=invoice,
            kind=InvoiceLine.Kind.AD_HOC,
            description=charge.description,
            amount=charge.amount,
            target=target,
        )
        charge.attached_invoice = invoice
        charge.save(update_fields=["attached_invoice", "updated_at"])

    # Tax — on rent + pro-rata portion (not on ad-hocs for simplicity)
    tax_type = get_effective_setting(tenancy.house, "tax_type")
    if tax_type is not None and tax_type.rate > 0:
        taxable = sum(
            (l.amount for l in invoice.lines.filter(
                kind__in=[InvoiceLine.Kind.RENT, InvoiceLine.Kind.PRORATA]
            )),
            Decimal("0"),
        )
        tax_amount = (taxable * tax_type.rate / Decimal("100")).quantize(Decimal("1"))
        if tax_amount > 0:
            InvoiceTaxLine.objects.create(
                invoice=invoice,
                tax_type=tax_type,
                taxable_amount=taxable,
                rate_percent=tax_type.rate,
                amount=tax_amount,
            )

    invoice.recalculate_totals()
    invoice.save(update_fields=["subtotal", "tax_total", "total", "updated_at"])

    _issue_and_post(invoice, user=user)

    # Try to auto-apply any held advances for this tenant
    try_apply_advance_to_invoice(invoice, user=user)

    return InvoiceDraft(invoice=invoice, created=True, posted=True)


def _issue_and_post(invoice, *, user=None):
    """DRAFT → ISSUED + accrual journal.

    Revenue routing:
    - Meili-owned, non-utility line     -> RENT_INCOME
    - Meili-owned, UTILITY line         -> matching utility income (4310-4390)
    - Managed, any landlord-target line -> LANDLORD_PAYABLE (landlord still
      earns the utility; per-utility line description preserves the break-out
      on the landlord statement)
    - Any Meili-target line (ad-hoc)    -> RENT_INCOME (Meili service income)
    """
    entry = JournalEntry.objects.create(
        entry_date=invoice.issue_date,
        memo=f"Invoice accrual (pending #{invoice.pk})",
        source=JournalEntry.Source.INVOICE,
        created_by=user,
    )
    ar = get_account(SYS_AR_TENANTS)
    # AR debit for the full invoice total
    JournalEntryLine.objects.create(
        entry=entry, account=ar, debit=invoice.total, credit=Decimal("0"),
        description=f"AR — tenant {invoice.tenant_house.tenant_id}",
    )
    landlord = invoice.tenant_house.house.effective_landlord
    meili_owned = bool(landlord and landlord.is_meili_owned)

    # Tallies
    landlord_non_utility_total = Decimal("0")
    landlord_utility_totals = {}  # utility_kind -> amount
    meili_total = Decimal("0")
    for line in invoice.lines.all():
        if line.target == InvoiceLine.TARGET_MEILI:
            meili_total += line.amount
            continue
        if line.kind == InvoiceLine.Kind.UTILITY and line.utility_kind:
            landlord_utility_totals[line.utility_kind] = (
                landlord_utility_totals.get(line.utility_kind, Decimal("0")) + line.amount
            )
        else:
            landlord_non_utility_total += line.amount

    # Non-utility landlord-side revenue
    if landlord_non_utility_total > 0:
        if meili_owned:
            JournalEntryLine.objects.create(
                entry=entry, account=get_account(SYS_RENT_INCOME),
                debit=Decimal("0"), credit=landlord_non_utility_total,
                description="Rent income (Meili-owned)",
            )
        else:
            JournalEntryLine.objects.create(
                entry=entry, account=get_account(SYS_LANDLORD_PAYABLE),
                debit=Decimal("0"), credit=landlord_non_utility_total,
                description="Landlord payable (managed)",
            )

    # Utility landlord-side revenue — break out to utility-income accounts
    # only for Meili-owned. Managed properties still route to landlord payable
    # (since the landlord is the one earning the utility fee).
    for utility_kind, amt in landlord_utility_totals.items():
        if amt <= 0:
            continue
        if meili_owned:
            syscode = UTILITY_INCOME_SYSCODE_BY_KIND[utility_kind]
            JournalEntryLine.objects.create(
                entry=entry, account=get_account(syscode),
                debit=Decimal("0"), credit=amt,
                description=f"{utility_kind} income (Meili-owned)",
            )
        else:
            JournalEntryLine.objects.create(
                entry=entry, account=get_account(SYS_LANDLORD_PAYABLE),
                debit=Decimal("0"), credit=amt,
                description=f"Landlord payable — {utility_kind} (managed)",
            )

    if meili_total > 0:
        JournalEntryLine.objects.create(
            entry=entry, account=get_account(SYS_RENT_INCOME),
            debit=Decimal("0"), credit=meili_total,
            description="Meili service/ad-hoc income",
        )
    if invoice.tax_total and invoice.tax_total > 0:
        JournalEntryLine.objects.create(
            entry=entry, account=get_account(SYS_TAX_PAYABLE),
            debit=Decimal("0"), credit=invoice.tax_total,
            description="Tax payable",
        )
    entry.post(user=user)
    invoice.source_journal = entry
    invoice.transition_to(Invoice.Status.ISSUED, save=False)
    invoice.save()


@transaction.atomic
def generate_invoices_for_due_tenancies(*, user=None, today=None):
    """Iterate active tenancies and generate any due invoices.

    Returns a summary dict — used by the management command + Celery task.
    """
    from core.models import TenantHouse
    today = today or timezone.localdate()
    qs = TenantHouse.objects.filter(
        status=TenantHouse.Status.ACTIVE,
        invoice_generation_status=TenantHouse.InvoiceGenerationStatus.ACTIVE,
    )
    created = 0
    skipped = 0
    paused = TenantHouse.objects.filter(
        invoice_generation_status__in=[
            TenantHouse.InvoiceGenerationStatus.PAUSED,
            TenantHouse.InvoiceGenerationStatus.STOPPED,
        ]
    ).count()
    errors = []
    for tenancy in qs.select_related("house", "house__estate"):
        try:
            result = generate_invoice_for_tenancy(tenancy, user=user, today=today)
            if result and result.created:
                created += 1
            else:
                skipped += 1
        except (ValidationError, InvoiceGenerationPaused) as exc:
            errors.append({"tenancy_id": tenancy.pk, "error": str(exc)})
    return {
        "created": created, "skipped": skipped,
        "paused": paused, "errors": errors,
    }


# ---------------------------------------------------------------------------
# Overdue sweep
# ---------------------------------------------------------------------------
@transaction.atomic
def mark_overdue_invoices(*, today=None):
    today = today or timezone.localdate()
    qs = Invoice.objects.filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
        due_date__lt=today,
    )
    count = 0
    for inv in qs:
        if inv.outstanding > 0:
            inv.transition_to(Invoice.Status.OVERDUE)
            count += 1
            try:
                from notifications.tasks import send_overdue_reminder
                send_overdue_reminder.delay(inv.pk)
            except Exception:
                pass
    return count


# ---------------------------------------------------------------------------
# Payment application — FIFO + advance routing
# ---------------------------------------------------------------------------
@transaction.atomic
def apply_payment(payment: Payment, *, user=None):
    """Apply an APPROVED (or AUTO_APPROVED) payment to outstanding invoices
    in FIFO order. Any surplus is routed to the tenant's held-advance account.

    Posts:
        Dr Bank/Cash   (the BankAccount.ledger_account)
        Cr AR_TENANTS  (for the portion applied to invoices)
        Cr Held Advance (for any surplus sitting on liability)
    """
    if not payment.is_effectively_approved:
        raise ValidationError("Payment must be approved before allocation.")
    if payment.source_journal_id:
        return  # already posted — idempotent

    remaining = Decimal(payment.amount)
    ar = get_account(SYS_AR_TENANTS)
    bank_ledger = payment.bank_account.ledger_account

    entry = JournalEntry.objects.create(
        entry_date=timezone.localdate(),
        memo=f"Payment (pending #{payment.pk})",
        source=JournalEntry.Source.PAYMENT,
        created_by=user,
    )
    JournalEntryLine.objects.create(
        entry=entry, account=bank_ledger,
        debit=payment.amount, credit=Decimal("0"),
        description=f"Payment received via {payment.get_method_display()}",
    )

    invoices = Invoice.objects.filter(
        tenant_house__tenant=payment.tenant,
        status__in=[
            Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE,
        ],
    ).order_by("due_date", "id")
    ar_applied = Decimal("0")
    advance_by_house = {}

    for inv in invoices:
        if remaining <= 0:
            break
        outstanding = inv.outstanding
        if outstanding <= 0:
            continue
        take = min(outstanding, remaining)
        PaymentAllocation.objects.create(
            payment=payment, invoice=inv, amount=take,
            is_advance_hold=False, applied_at=timezone.now(),
        )
        ar_applied += take
        remaining -= take
        # Transition invoice state based on paid-off status
        new_outstanding = outstanding - take
        if new_outstanding == 0:
            inv.transition_to(Invoice.Status.PAID)
        elif inv.status != Invoice.Status.PARTIALLY_PAID:
            inv.transition_to(Invoice.Status.PARTIALLY_PAID)
        # Recognise commission proportional to rent applied on this invoice
        recognize_commission_on_allocation(inv, amount_applied=take, user=user)

    if ar_applied > 0:
        JournalEntryLine.objects.create(
            entry=entry, account=ar,
            debit=Decimal("0"), credit=ar_applied,
            description="AR settlement",
        )

    if remaining > 0:
        # Route surplus to the correct held-advance account. If the tenant
        # has multiple active tenancies, pool surplus under the first one's
        # house (edge case — documented as tech debt).
        active_tenancy = (
            payment.tenant.tenancies.filter(status="ACTIVE")
            .select_related("house").first()
        )
        if active_tenancy is None:
            raise ValidationError(
                "Cannot hold advance: tenant has no active tenancy."
            )
        held_account = get_advance_holding_account(active_tenancy.house)
        PaymentAllocation.objects.create(
            payment=payment, invoice=None, amount=remaining,
            is_advance_hold=True,
        )
        JournalEntryLine.objects.create(
            entry=entry, account=held_account,
            debit=Decimal("0"), credit=remaining,
            description="Tenant advance held",
        )

    entry.memo = f"Payment allocation {payment.pk}"
    entry.save(update_fields=["memo"])
    entry.post(user=user)
    # Assign payment number + journal
    if not payment.number:
        payment.number = allocate_number("RCP")
    payment.source_journal = entry
    payment.save(update_fields=["number", "source_journal"])

    # Issue a Receipt record
    Receipt.objects.create(
        number=allocate_number("RCP"),
        kind=Receipt.Kind.PAYMENT,
        payment=payment,
        amount=payment.amount,
        created_by=user,
    )

    # Fire confirmation notification — best-effort, never block the ledger.
    try:
        from notifications.tasks import send_payment_confirmation
        send_payment_confirmation.delay(payment.pk)
    except Exception:
        pass


@transaction.atomic
def try_apply_advance_to_invoice(invoice, *, user=None):
    """If the tenant has held-advance balance, consume it FIFO against this
    newly-issued invoice. Posts: Dr Held Advance, Cr AR_TENANTS.
    """
    tenant = invoice.tenant_house.tenant
    holds = PaymentAllocation.objects.filter(
        payment__tenant=tenant,
        is_advance_hold=True,
        applied_at__isnull=True,
    ).select_related("payment").order_by("allocated_at", "id")
    if not holds.exists():
        return
    ar = get_account(SYS_AR_TENANTS)
    held_account = get_advance_holding_account(invoice.tenant_house.house)

    entry = None
    for hold in holds:
        remaining_inv = invoice.outstanding
        if remaining_inv <= 0:
            break
        take = min(hold.amount, remaining_inv)
        if entry is None:
            entry = JournalEntry.objects.create(
                reference=allocate_number("JE"),
                entry_date=timezone.localdate(),
                memo=f"Advance applied to invoice {invoice.pk}",
                source=JournalEntry.Source.PAYMENT,
                created_by=user,
            )
        # Partial split: if the hold exceeds what the invoice can absorb, split.
        if take == hold.amount:
            hold.invoice = invoice
            hold.is_advance_hold = False
            hold.applied_at = timezone.now()
            hold.save(update_fields=["invoice", "is_advance_hold", "applied_at"])
        else:
            # consume `take` from this hold, leave remainder as new hold row
            PaymentAllocation.objects.create(
                payment=hold.payment, invoice=invoice, amount=take,
                is_advance_hold=False, applied_at=timezone.now(),
            )
            hold.amount = hold.amount - take
            hold.save(update_fields=["amount"])
        JournalEntryLine.objects.create(
            entry=entry, account=held_account,
            debit=take, credit=Decimal("0"),
            description="Advance released",
        )
        JournalEntryLine.objects.create(
            entry=entry, account=ar,
            debit=Decimal("0"), credit=take,
            description=f"Applied to invoice {invoice.number or invoice.pk}",
        )
        # State transitions
        new_outstanding = invoice.outstanding
        if new_outstanding == 0:
            invoice.transition_to(Invoice.Status.PAID)
        elif invoice.status != Invoice.Status.PARTIALLY_PAID:
            invoice.transition_to(Invoice.Status.PARTIALLY_PAID)
        recognize_commission_on_allocation(invoice, amount_applied=take, user=user)

    if entry is not None:
        entry.post(user=user)


# ---------------------------------------------------------------------------
# Commission recognition
# ---------------------------------------------------------------------------
def _commission_config(house):
    c_type = get_effective_setting(house, "commission_type")
    c_amt = get_effective_setting(house, "commission_amount") or Decimal("0")
    c_pct = get_effective_setting(house, "commission_percent") or Decimal("0")
    return c_type, Decimal(c_amt), Decimal(c_pct)


def _rent_portion_of(invoice, amount_applied):
    """Apportion `amount_applied` across the rent+prorata lines only. Commission
    is earned on rent collection, not on ad-hocs or tax."""
    total = invoice.total
    if total <= 0:
        return Decimal("0")
    rent_total = sum(
        (l.amount for l in invoice.lines.filter(
            kind__in=[InvoiceLine.Kind.RENT, InvoiceLine.Kind.PRORATA],
            target=InvoiceLine.TARGET_LANDLORD,
        )),
        Decimal("0"),
    )
    if rent_total == 0:
        return Decimal("0")
    return (Decimal(amount_applied) * rent_total / total).quantize(Decimal("1"))


@transaction.atomic
def recognize_commission_on_allocation(invoice, *, amount_applied, user=None):
    """Post commission proportional to the rent portion of `amount_applied`.

    Meili-owned properties skip commission entirely (Meili already owns the
    rent income). For managed properties, commission moves an amount from
    LANDLORD_PAYABLE → COMMISSION_INCOME.
    """
    landlord = invoice.tenant_house.house.effective_landlord
    if landlord is None or landlord.is_meili_owned:
        return None
    rent_applied = _rent_portion_of(invoice, amount_applied)
    if rent_applied <= 0:
        return None
    c_type, c_amt, c_pct = _commission_config(invoice.tenant_house.house)
    if not c_type:
        return None
    # How much total rent on this invoice (landlord-target rent/prorata)
    rent_total = sum(
        (l.amount for l in invoice.lines.filter(
            kind__in=[InvoiceLine.Kind.RENT, InvoiceLine.Kind.PRORATA],
            target=InvoiceLine.TARGET_LANDLORD,
        )),
        Decimal("0"),
    )
    if rent_total <= 0:
        return None
    if c_type == "PERCENTAGE":
        commission = (rent_applied * c_pct / Decimal("100")).quantize(Decimal("1"))
    else:  # FIXED — apportion pro rata over rent_total
        commission = (c_amt * rent_applied / rent_total).quantize(Decimal("1"))
    if commission <= 0:
        return None
    entry = JournalEntry.objects.create(
        entry_date=timezone.localdate(),
        memo=f"Commission on invoice {invoice.number or invoice.pk}",
        source=JournalEntry.Source.COMMISSION,
        created_by=user,
    )
    JournalEntryLine.objects.create(
        entry=entry, account=get_account(SYS_LANDLORD_PAYABLE),
        debit=commission, credit=Decimal("0"),
        description="Commission withheld from landlord",
    )
    JournalEntryLine.objects.create(
        entry=entry, account=get_account(SYS_COMMISSION_INCOME),
        debit=Decimal("0"), credit=commission,
        description="Commission income recognised",
    )
    entry.post(user=user)
    CommissionPosting.objects.create(
        invoice=invoice, journal_entry=entry, amount=commission,
    )
    return entry


# ---------------------------------------------------------------------------
# Void workflow
# ---------------------------------------------------------------------------
@transaction.atomic
def execute_void(void: InvoiceVoid, *, user=None):
    """Run the post-approval void effect: reverse accrual + commission
    postings, unapply payments, transition invoice to VOIDED.
    """
    if not void.is_effectively_approved:
        raise ValidationError("Void must be approved before execution.")
    if void.reversing_journal_id:
        return  # idempotent
    invoice = void.invoice
    if invoice.source_journal_id:
        reversal = invoice.source_journal.reverse(
            user=user,
            memo=f"Void of invoice {invoice.number or invoice.pk}",
        )
        void.reversing_journal = reversal
    # Reverse every commission posting attached to this invoice
    for cp in invoice.commission_postings.filter(is_reversal=False, reverses__isnull=True):
        je_reversal = cp.journal_entry.reverse(
            user=user,
            memo=f"Commission reversal (void) invoice {invoice.number or invoice.pk}",
        )
        CommissionPosting.objects.create(
            invoice=invoice, journal_entry=je_reversal,
            amount=cp.amount, is_reversal=True, reverses=cp,
        )
    # Detach payment allocations — credit the amounts back to held-advance
    allocations = invoice.allocations.all()
    if allocations.exists():
        holding = get_advance_holding_account(invoice.tenant_house.house)
        ar = get_account(SYS_AR_TENANTS)
        entry = JournalEntry.objects.create(
            entry_date=timezone.localdate(),
            memo=f"Payments unapplied from voided invoice {invoice.number or invoice.pk}",
            source=JournalEntry.Source.VOID,
            created_by=user,
        )
        unapplied_total = Decimal("0")
        for alloc in allocations:
            unapplied_total += alloc.amount
            # Rewind allocation back to held-advance
            alloc.invoice = None
            alloc.is_advance_hold = True
            alloc.applied_at = None
            alloc.save(update_fields=["invoice", "is_advance_hold", "applied_at"])
        if unapplied_total > 0:
            JournalEntryLine.objects.create(
                entry=entry, account=ar, debit=unapplied_total,
                credit=Decimal("0"), description="AR restored (payments detached)",
            )
            JournalEntryLine.objects.create(
                entry=entry, account=holding, debit=Decimal("0"),
                credit=unapplied_total, description="Advance re-held",
            )
            entry.post(user=user)
    invoice.transition_to(Invoice.Status.VOIDED)
    void.save(update_fields=["reversing_journal", "updated_at"])


# ---------------------------------------------------------------------------
# Credit-note workflow
# ---------------------------------------------------------------------------
@transaction.atomic
def execute_credit_note(cn: CreditNote, *, user=None):
    """Post the credit note: reduces AR and reverses proportional commission."""
    if not cn.is_effectively_approved:
        raise ValidationError("Credit note must be approved before execution.")
    if cn.source_journal_id:
        return
    invoice = cn.original_invoice
    landlord = invoice.tenant_house.house.effective_landlord
    meili_owned = bool(landlord and landlord.is_meili_owned)

    entry = JournalEntry.objects.create(
        entry_date=timezone.localdate(),
        memo=f"Credit note {cn.amount} on invoice {invoice.number or invoice.pk}",
        source=JournalEntry.Source.CREDIT_NOTE,
        created_by=user,
    )
    # Reduce AR (credit) and reduce revenue/landlord-payable (debit)
    JournalEntryLine.objects.create(
        entry=entry, account=get_account(SYS_AR_TENANTS),
        debit=Decimal("0"), credit=cn.amount,
        description="AR reduction via credit note",
    )
    if meili_owned:
        JournalEntryLine.objects.create(
            entry=entry, account=get_account(SYS_RENT_INCOME),
            debit=cn.amount, credit=Decimal("0"),
            description="Revenue reversal",
        )
    else:
        JournalEntryLine.objects.create(
            entry=entry, account=get_account(SYS_LANDLORD_PAYABLE),
            debit=cn.amount, credit=Decimal("0"),
            description="Landlord payable reduction",
        )
    entry.post(user=user)
    cn.source_journal = entry
    if not cn.number:
        cn.number = allocate_number("CRN")
    cn.save(update_fields=["source_journal", "number", "updated_at"])

    # Proportional commission reversal — only on managed properties and only
    # on the rent portion implied by the credit.
    if not meili_owned:
        invoice_rent_total = sum(
            (l.amount for l in invoice.lines.filter(
                kind__in=[InvoiceLine.Kind.RENT, InvoiceLine.Kind.PRORATA],
                target=InvoiceLine.TARGET_LANDLORD,
            )),
            Decimal("0"),
        )
        already_recognised = sum(
            (cp.amount if not cp.is_reversal else -cp.amount
             for cp in invoice.commission_postings.all()),
            Decimal("0"),
        )
        if invoice_rent_total > 0 and already_recognised > 0:
            # reverse proportional to credit_amount / invoice.total
            portion = (
                already_recognised * Decimal(cn.amount) / invoice.total
            ).quantize(Decimal("1"))
            if portion > 0:
                rev = JournalEntry.objects.create(
                    entry_date=timezone.localdate(),
                    memo=f"Commission reversal (credit note) inv {invoice.number or invoice.pk}",
                    source=JournalEntry.Source.COMMISSION,
                    created_by=user,
                )
                JournalEntryLine.objects.create(
                    entry=rev, account=get_account(SYS_COMMISSION_INCOME),
                    debit=portion, credit=Decimal("0"),
                    description="Commission reversal",
                )
                JournalEntryLine.objects.create(
                    entry=rev, account=get_account(SYS_LANDLORD_PAYABLE),
                    debit=Decimal("0"), credit=portion,
                    description="Landlord payable restored",
                )
                rev.post(user=user)
                CommissionPosting.objects.create(
                    invoice=invoice, journal_entry=rev,
                    amount=portion, is_reversal=True,
                )


# ---------------------------------------------------------------------------
# Refund workflow
# ---------------------------------------------------------------------------
@transaction.atomic
def execute_refund(refund: Refund, *, user=None):
    """Post the refund journal: Dr source_account, Cr bank/cash."""
    if not refund.is_effectively_approved:
        raise ValidationError("Refund must be approved before execution.")
    if refund.refund_journal_id:
        return
    if refund.bank_account is None and refund.method != Refund.Method.CASH:
        raise ValidationError("Non-cash refund requires a bank_account.")

    entry = JournalEntry.objects.create(
        entry_date=timezone.localdate(),
        memo=f"Refund {refund.amount} to tenant {refund.tenant_id}",
        source=JournalEntry.Source.REFUND,
        created_by=user,
    )
    JournalEntryLine.objects.create(
        entry=entry, account=refund.source_account,
        debit=refund.amount, credit=Decimal("0"),
        description=f"Refund ex {refund.get_source_display()}",
    )
    if refund.bank_account is not None:
        JournalEntryLine.objects.create(
            entry=entry, account=refund.bank_account.ledger_account,
            debit=Decimal("0"), credit=refund.amount,
            description=f"Refund paid via {refund.get_method_display()}",
        )
    else:
        # Cash refund — go through cash-on-hand
        from accounting.utils import SYS_CASH
        JournalEntryLine.objects.create(
            entry=entry, account=get_account(SYS_CASH),
            debit=Decimal("0"), credit=refund.amount,
            description="Refund paid in cash",
        )
    entry.post(user=user)
    refund.refund_journal = entry
    if not refund.number:
        refund.number = allocate_number("REF")
    refund.save(update_fields=["refund_journal", "number", "updated_at"])

    Receipt.objects.create(
        number=allocate_number("RCP"),
        kind=Receipt.Kind.REFUND,
        refund=refund,
        amount=refund.amount,
        created_by=user,
    )
