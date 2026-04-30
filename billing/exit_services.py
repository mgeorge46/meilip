"""Tenant-exit settlement service — SPEC §20.5 strict-order closeout.

Strict order of application:

    1. Pay off outstanding invoices on the exiting tenancy using held-advance
       balance (both the MANAGED and MEILI held-advance accounts, whichever
       applies to this tenancy).
    2. Apply damages / ad-hoc charges posted as part of the exit.
    3. OPTIONAL (employee approval): transfer residual held-advance to the
       tenant's OTHER active tenancies. Cross-ownership transfers (moving
       funds from MANAGED → MEILI or vice versa) are handled by re-routing
       through the correct held-advance account per tenancy.
    4. Refund the remainder to the tenant via a maker-checker Refund.

All journal entries are balanced double-entry. Held-advance accounts are
never merged; the holding account is resolved per tenant-house via
`get_advance_holding_account(house)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounting.models import JournalEntry, JournalEntryLine
from accounting.utils import (
    SYS_AR_TENANTS,
    SYS_SECURITY_DEPOSIT_HELD,
    SYS_SECURITY_DEPOSIT_REFUNDABLE,
    get_account,
    get_advance_holding_account,
)
from core.models import TenantHouse

from .exceptions import SelfApprovalBlocked
from .models import (
    AdHocCharge,
    ApprovalStatus,
    ExitSettlement,
    Invoice,
    PaymentAllocation,
    Refund,
    SecurityDeposit,
    SecurityDepositMovement,
)
from .sequences import allocate_number
from .services import execute_refund, try_apply_advance_to_invoice


# ---------------------------------------------------------------------------
# Computation (read-only — safe to call from the UI to preview a plan).
# ---------------------------------------------------------------------------
@dataclass
class ExitComputation:
    tenant_house: TenantHouse
    held_managed: Decimal = Decimal("0")
    held_meili: Decimal = Decimal("0")
    deposit_balance: Decimal = Decimal("0")
    outstanding_invoices: list = field(default_factory=list)  # [(invoice, outstanding)]
    outstanding_total: Decimal = Decimal("0")

    @property
    def total_held(self) -> Decimal:
        return self.held_managed + self.held_meili


def _tenant_held_balance(tenant, ownership: str) -> Decimal:
    """Sum of un-applied advance holds for a tenant, routed to either the
    MANAGED or MEILI held-advance account."""
    qs = PaymentAllocation.objects.filter(
        payment__tenant=tenant,
        is_advance_hold=True,
        applied_at__isnull=True,
    ).select_related("payment")
    total = Decimal("0")
    for alloc in qs:
        # Walk back through payment → tenant_house → house → landlord to
        # decide which account the funds live on. A payment's "home" is
        # the first active tenancy at the time it was received, so we
        # group by tenancy to ensure accuracy on multi-tenancy tenants.
        # For MVP we check via the tenant's active tenancies.
        for th in alloc.payment.tenant.tenancies.filter(status=TenantHouse.Status.ACTIVE):
            landlord = th.house.effective_landlord
            meili_owned = bool(landlord and landlord.is_meili_owned)
            alloc_owns = "MEILI" if meili_owned else "MANAGED"
            if alloc_owns == ownership:
                total += Decimal(alloc.amount)
                break
    return total


def compute_exit_settlement(
    tenant_house: TenantHouse, *, damages: Optional[List[dict]] = None
) -> ExitComputation:
    """Compute the step-by-step settlement preview for `tenant_house`.

    `damages` is an optional list of dicts `{"description": str, "amount": Decimal}`
    that the employee is adding at exit time (will be written as AdHocCharge
    rows on execute).
    """
    tenant = tenant_house.tenant
    comp = ExitComputation(tenant_house=tenant_house)
    comp.held_managed = _tenant_held_balance(tenant, "MANAGED")
    comp.held_meili = _tenant_held_balance(tenant, "MEILI")

    # Deposit — lifecycle row or fallback to raw TenantHouse.security_deposit
    try:
        dep = tenant_house.security_deposit_record
        comp.deposit_balance = dep.balance
    except SecurityDeposit.DoesNotExist:
        comp.deposit_balance = Decimal(tenant_house.security_deposit or 0)

    # Outstanding invoices on this tenancy.
    for inv in tenant_house.invoices.filter(
        status__in=[
            Invoice.Status.ISSUED,
            Invoice.Status.PARTIALLY_PAID,
            Invoice.Status.OVERDUE,
        ]
    ).order_by("due_date", "id"):
        out = inv.outstanding
        if out > 0:
            comp.outstanding_invoices.append((inv, out))
            comp.outstanding_total += out

    comp.damages_total = sum(
        (Decimal(d["amount"]) for d in (damages or [])), Decimal("0")
    )
    return comp


# ---------------------------------------------------------------------------
# Planning: deterministic strict-order plan produced from a computation.
# ---------------------------------------------------------------------------
def build_settlement_plan(
    comp: ExitComputation,
    *,
    damages: Optional[List[dict]] = None,
    transfer_to_tenancy_ids: Optional[List[int]] = None,
) -> dict:
    """Produce a strict-order plan JSON from the computation + inputs.

    The plan is the source of truth for the execute step — it is stored on
    the ExitSettlement row so maker/checker can see exactly what will happen.
    """
    damages = damages or []
    transfer_to_tenancy_ids = transfer_to_tenancy_ids or []

    available_managed = comp.held_managed
    available_meili = comp.held_meili

    # Step 1 — apply held advance to outstanding invoices (use the matching
    # ownership holding account; if this tenancy is managed, prefer managed
    # advance first and fall back to meili ONLY if the shortfall is real,
    # but cross-ownership inside a single invoice is an accounting anti-
    # pattern; we constrain strictly to the tenancy's own ownership.)
    landlord = comp.tenant_house.house.effective_landlord
    this_ownership = "MEILI" if landlord and landlord.is_meili_owned else "MANAGED"
    step1 = []
    remaining_outstanding = comp.outstanding_total
    take_from_this = min(
        remaining_outstanding,
        available_meili if this_ownership == "MEILI" else available_managed,
    )
    if take_from_this > 0:
        step1.append({
            "ownership": this_ownership,
            "amount": str(take_from_this),
        })
        if this_ownership == "MEILI":
            available_meili -= take_from_this
        else:
            available_managed -= take_from_this
        remaining_outstanding -= take_from_this

    # Step 2 — apply damages/ad-hoc (billed as AdHocCharge, then consumed
    # from remaining held-advance same-ownership-first).
    step2 = []
    damages_left = sum(
        (Decimal(d["amount"]) for d in damages), Decimal("0")
    )
    if damages_left > 0:
        primary_pool = (
            available_meili if this_ownership == "MEILI" else available_managed
        )
        take_from_damages = min(damages_left, primary_pool)
        if take_from_damages > 0:
            step2.append({
                "ownership": this_ownership,
                "amount": str(take_from_damages),
            })
            if this_ownership == "MEILI":
                available_meili -= take_from_damages
            else:
                available_managed -= take_from_damages
            damages_left -= take_from_damages

    # Step 3 — optional transfer to other active tenancies. Each transfer
    # moves funds from the exiting tenancy's ownership pool into the target
    # tenancy's ownership pool (cross-ownership allowed, but each side gets
    # its own journal line on the correct held-advance account).
    step3 = []
    # Cap remaining that can be transferred to each.
    pool_remaining = available_managed + available_meili
    for tid in transfer_to_tenancy_ids:
        try:
            target_th = TenantHouse.objects.get(
                pk=tid, tenant=comp.tenant_house.tenant,
                status=TenantHouse.Status.ACTIVE,
            )
        except TenantHouse.DoesNotExist:
            continue
        if target_th.pk == comp.tenant_house.pk:
            continue
        if pool_remaining <= 0:
            break
        # Move whatever is left — employee can edit this per-tenancy in UI.
        step3.append({
            "target_tenancy_id": target_th.pk,
            "amount": str(pool_remaining),
            "source_ownership": this_ownership,
            "target_ownership": (
                "MEILI" if target_th.house.effective_landlord
                and target_th.house.effective_landlord.is_meili_owned
                else "MANAGED"
            ),
        })
        # For computation purposes, transfer drains the pool.
        available_managed = Decimal("0") if this_ownership == "MANAGED" else available_managed
        available_meili = Decimal("0") if this_ownership == "MEILI" else available_meili
        pool_remaining = Decimal("0")
        break  # only first target honoured by plan — UI can add more before execute

    # Step 4 — refund the remainder.
    refund_amount = available_managed + available_meili
    # Also: if deposit balance > 0 AND no damages/outstanding consumed it,
    # refund it via SECURITY_DEPOSIT_HELD → cash.
    deposit_refund = max(Decimal("0"), comp.deposit_balance - damages_left)
    deposit_applied_to_damages = comp.deposit_balance - deposit_refund
    damages_left -= deposit_applied_to_damages

    landlord_shortfall = max(
        Decimal("0"), remaining_outstanding + damages_left
    )

    plan = {
        "tenant_house_id": comp.tenant_house.pk,
        "ownership": this_ownership,
        "held_managed_start": str(comp.held_managed),
        "held_meili_start": str(comp.held_meili),
        "deposit_start": str(comp.deposit_balance),
        "outstanding_start": str(comp.outstanding_total),
        "damages": [
            {"description": d["description"], "amount": str(d["amount"])}
            for d in damages
        ],
        "damages_total": str(comp.damages_total),
        "step_1_apply_to_invoices": step1,
        "step_2_apply_to_damages": step2,
        "step_2_deposit_applied_to_damages": str(deposit_applied_to_damages),
        "step_3_transfers": step3,
        "step_4_refund": {
            "advance_remainder": str(refund_amount),
            "deposit_remainder": str(deposit_refund),
            "total": str(refund_amount + deposit_refund),
        },
        "landlord_shortfall": str(landlord_shortfall),
    }
    return plan


# ---------------------------------------------------------------------------
# Execution — writes journal entries, transitions invoices, creates the
# Refund row (pending its own maker-checker cycle).
# ---------------------------------------------------------------------------
@transaction.atomic
def execute_exit_settlement(
    settlement: ExitSettlement, *,
    refund_method: str,
    refund_bank_account,
    refund_destination: str,
    refund_reference: str,
    damages_input: Optional[List[dict]] = None,
    user=None,
) -> ExitSettlement:
    """Apply the plan stored on `settlement`.

    Preconditions:
      - settlement.approval_status is APPROVED (maker-checker on the envelope).
      - The underlying Refund created at step 4 then goes through its OWN
        maker-checker cycle before `execute_refund` runs.
    """
    if not settlement.is_effectively_approved:
        raise ValidationError("Exit settlement must be approved before execution.")
    if settlement.status != ExitSettlement.Status.DRAFT:
        raise ValidationError(
            f"Exit settlement {settlement.pk} is {settlement.status}, not DRAFT."
        )

    plan = settlement.plan or {}
    th = settlement.tenant_house
    tenant = th.tenant
    damages_input = damages_input or []

    # --- Step 2a: write the damages as AdHocCharge rows (already approved as
    # part of the exit envelope), attach nothing — they'll be invoiced on
    # the final exit invoice (one-shot) or consumed via deposit/advance.
    damage_charges: List[AdHocCharge] = []
    for d in damages_input:
        ch = AdHocCharge.objects.create(
            tenant_house=th,
            description=d["description"],
            amount=Decimal(d["amount"]),
            target=AdHocCharge.Target.LANDLORD,
            bill_on_or_after=timezone.localdate(),
            approval_status=ApprovalStatus.AUTO_APPROVED,
            approved_at=timezone.now(),
            maker=user,
            created_by=user,
        )
        damage_charges.append(ch)

    # If damages exist, accumulate them into a final one-shot exit invoice so
    # that AR + landlord payable stays consistent. This is billed, not
    # generated by the usual cycle machinery.
    exit_invoice = None
    if damage_charges:
        today = timezone.localdate()
        damages_total = sum((c.amount for c in damage_charges), Decimal("0"))
        exit_invoice = Invoice.objects.create(
            tenant_house=th,
            period_from=today,
            period_to=today,
            issue_date=today,
            due_date=today,
            rent_amount=Decimal("0"),
            subtotal=damages_total,
            tax_total=Decimal("0"),
            total=damages_total,
            created_by=user,
            notes="Exit settlement damages",
        )
        from .models import InvoiceLine
        for ch in damage_charges:
            InvoiceLine.objects.create(
                invoice=exit_invoice,
                kind=InvoiceLine.Kind.AD_HOC,
                description=ch.description,
                amount=ch.amount,
                target=InvoiceLine.TARGET_LANDLORD,
            )
            ch.attached_invoice = exit_invoice
            ch.save(update_fields=["attached_invoice", "updated_at"])
        # Issue + post the accrual using the existing billing service.
        from .services import _issue_and_post
        _issue_and_post(exit_invoice, user=user)

    # --- Step 1 & 2b: apply held-advance to outstanding invoices (current
    # ones first, then the exit invoice if present).
    outstanding_invs = list(
        th.invoices.filter(
            status__in=[
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.OVERDUE,
            ]
        ).order_by("due_date", "id")
    )
    for inv in outstanding_invs:
        try_apply_advance_to_invoice(inv, user=user)

    # --- Step 2c: if damages left unpaid AND deposit balance > 0, apply
    # deposit to remaining open balance on the exit invoice (then older ones).
    try:
        deposit = th.security_deposit_record
    except SecurityDeposit.DoesNotExist:
        deposit = None

    if deposit and deposit.balance > 0:
        remaining_ar = Decimal("0")
        for inv in th.invoices.filter(
            status__in=[
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.OVERDUE,
            ]
        ).order_by("due_date", "id"):
            take = min(deposit.balance, inv.outstanding)
            if take <= 0:
                continue
            _apply_deposit_to_invoice(deposit, inv, take, user=user)
            remaining_ar += take
            if deposit.balance <= 0:
                break

    # --- Step 3: cross-tenancy transfers.
    for t in plan.get("step_3_transfers", []):
        _execute_transfer_between_held_accounts(
            tenant=tenant,
            source_house=th.house,
            target_tenancy_id=t["target_tenancy_id"],
            amount=Decimal(t["amount"]),
            user=user,
        )

    # --- Step 4: refund the remainder. Build Refund rows — NOT executed
    # here. Refund goes through its own maker-checker cycle.
    final_refund_amount = Decimal(
        plan.get("step_4_refund", {}).get("total", "0")
    )
    refund = None
    if final_refund_amount > 0:
        # Choose the source account — if there's still held-advance on this
        # tenant, route from the tenancy's holding account; else from
        # SECURITY_DEPOSIT_REFUNDABLE.
        source_account = (
            get_advance_holding_account(th.house)
            if Decimal(plan.get("step_4_refund", {}).get("advance_remainder", "0")) > 0
            else get_account(SYS_SECURITY_DEPOSIT_REFUNDABLE)
        )
        refund = Refund.objects.create(
            tenant=tenant,
            tenant_house=th,
            amount=final_refund_amount,
            method=refund_method,
            source=Refund.Source.HELD_ADVANCE if source_account.system_code.startswith(
                "TENANT_ADVANCE_HELD"
            ) else Refund.Source.SECURITY_DEPOSIT,
            source_account=source_account,
            bank_account=refund_bank_account,
            destination_details=refund_destination,
            reference_number=refund_reference,
            reason="Tenant exit settlement — strict-order refund (§20.5)",
            maker=user,
            approval_status=ApprovalStatus.PENDING,
            created_by=user,
        )

    # --- Finalise the envelope.
    settlement.refund = refund
    settlement.final_refund_amount = final_refund_amount
    settlement.landlord_shortfall = Decimal(plan.get("landlord_shortfall", "0"))
    settlement.status = ExitSettlement.Status.EXECUTED
    settlement.executed_at = timezone.now()
    settlement.executed_by = user
    settlement.save(update_fields=[
        "refund", "final_refund_amount", "landlord_shortfall",
        "status", "executed_at", "executed_by", "updated_at",
    ])

    # Mark the tenancy itself exited and stop the recurring-invoice generator
    # so Celery beat skips it next cycle.
    if th.status != TenantHouse.Status.EXITED:
        th.status = TenantHouse.Status.EXITED
        th.move_out_date = th.move_out_date or timezone.localdate()
        th.invoice_generation_status = TenantHouse.InvoiceGenerationStatus.STOPPED
        th.updated_by = user
        th.save(update_fields=[
            "status", "move_out_date", "invoice_generation_status",
            "updated_by", "updated_at",
        ])

    return settlement


# ---------------------------------------------------------------------------
# Deposit application & refund helpers.
# ---------------------------------------------------------------------------
def _apply_deposit_to_invoice(
    deposit: SecurityDeposit, invoice: Invoice, amount: Decimal, *, user=None
):
    """Apply `amount` of held security deposit to an outstanding invoice.

    Journal:
        Dr SECURITY_DEPOSIT_HELD     (liability down)
        Cr AR_TENANTS                (AR down)
    """
    if amount <= 0:
        return
    if amount > deposit.balance:
        amount = deposit.balance
    entry = JournalEntry.objects.create(
        entry_date=timezone.localdate(),
        memo=f"Deposit applied to invoice {invoice.number or invoice.pk}",
        source=JournalEntry.Source.REFUND,  # re-use nearest enum
        created_by=user,
    )
    JournalEntryLine.objects.create(
        entry=entry, account=get_account(SYS_SECURITY_DEPOSIT_HELD),
        debit=amount, credit=Decimal("0"),
        description="Security deposit applied",
    )
    JournalEntryLine.objects.create(
        entry=entry, account=get_account(SYS_AR_TENANTS),
        debit=Decimal("0"), credit=amount,
        description=f"AR settlement on invoice {invoice.number or invoice.pk}",
    )
    entry.post(user=user)

    deposit.amount_applied = Decimal(deposit.amount_applied or 0) + amount
    deposit.recompute_status()
    deposit.save(update_fields=["amount_applied", "status", "updated_at"])

    SecurityDepositMovement.objects.create(
        deposit=deposit,
        kind=SecurityDepositMovement.Kind.APPLY_INVOICE,
        amount=amount,
        invoice=invoice,
        journal_entry=entry,
        memo=f"Deposit → invoice {invoice.number or invoice.pk}",
    )
    # Close the invoice if fully settled.
    if invoice.outstanding <= 0:
        invoice.transition_to(Invoice.Status.PAID)
    elif invoice.status != Invoice.Status.PARTIALLY_PAID:
        invoice.transition_to(Invoice.Status.PARTIALLY_PAID)


def _execute_transfer_between_held_accounts(
    *, tenant, source_house, target_tenancy_id, amount: Decimal, user=None
):
    """Transfer `amount` from the tenant's advance holdings attributed to
    `source_house` over to `target_tenancy_id`'s holding account.

    Cross-ownership (MANAGED → MEILI or vice-versa) allowed: both legs of
    the journal entry hit the correct system account, so the two held
    balances stay separated even after the move.
    """
    if amount <= 0:
        return
    target_th = TenantHouse.objects.get(pk=target_tenancy_id)
    source_acc = get_advance_holding_account(source_house)
    target_acc = get_advance_holding_account(target_th.house)

    if source_acc.pk == target_acc.pk:
        # Same-ownership: no journal needed (both sides on same account),
        # just rewire allocations.
        _rewire_holds_to_tenancy(tenant, amount, target_th)
        return

    entry = JournalEntry.objects.create(
        entry_date=timezone.localdate(),
        memo=f"Advance transfer to tenancy {target_th.pk}",
        source=JournalEntry.Source.PAYMENT,
        created_by=user,
    )
    JournalEntryLine.objects.create(
        entry=entry, account=source_acc,
        debit=amount, credit=Decimal("0"),
        description=f"Advance released from {source_house}",
    )
    JournalEntryLine.objects.create(
        entry=entry, account=target_acc,
        debit=Decimal("0"), credit=amount,
        description=f"Advance re-held on {target_th.house}",
    )
    entry.post(user=user)
    _rewire_holds_to_tenancy(tenant, amount, target_th)


def _rewire_holds_to_tenancy(tenant, amount: Decimal, target_th: TenantHouse):
    """Best-effort: mark outstanding advance allocations as belonging to
    `target_th` so future invoice generation there picks them up via the
    existing `try_apply_advance_to_invoice` hook."""
    remaining = Decimal(amount)
    holds = PaymentAllocation.objects.filter(
        payment__tenant=tenant,
        is_advance_hold=True,
        applied_at__isnull=True,
    ).order_by("allocated_at", "id")
    for h in holds:
        if remaining <= 0:
            break
        take = min(remaining, Decimal(h.amount))
        if take == Decimal(h.amount):
            remaining -= take
            # leave this row — it still represents unapplied advance; the
            # journal has re-located the money to the target account
            continue
        h.amount = Decimal(h.amount) - take
        h.save(update_fields=["amount"])
        remaining -= take
