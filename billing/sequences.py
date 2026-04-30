"""Per-prefix per-month sequence numbers with atomic allocation.

Numbers are never reused. Voided invoices keep their number — gaps in a
prefix's sequence therefore indicate void events and are an auditable
signal. Concurrency-safe via SELECT ... FOR UPDATE on the sequence row.
"""
import re
from django.db import IntegrityError, models, transaction
from django.utils import timezone


class NumberSequence(models.Model):
    """Stores the next integer in a (prefix, year, month) bucket."""
    prefix = models.CharField(max_length=16)
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    next_value = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["prefix", "year", "month"], name="uniq_sequence_prefix_period"
            )
        ]
        ordering = ["-year", "-month", "prefix"]

    def __str__(self):
        return f"{self.prefix}-{self.year}{self.month:02d} next={self.next_value}"


PAD_WIDTH = {
    "INV": 5,
    "CRN": 5,
    "REF": 5,
    "RCP": 5,
    "JE": 6,
    "LPO": 5,   # Landlord Payout
    "SPY": 5,   # Supplier Payment
    "EXP": 5,   # Expense Claim
}


# Registry: prefix -> (lazy-loaded model class, reference field name)
# Used by `_resync_sequence_from_actuals` to repair sequence drift caused by
# rows whose reference was set directly (bypassing allocate_number) — e.g.
# the legacy journal-entry create flow that hand-sets `JournalEntry.reference`.
def _ref_models():
    from accounting.models import JournalEntry
    from .models import (
        Invoice, Refund, Receipt, CreditNote, LandlordPayout,
        SupplierPayment, ExpenseClaim, Payment,
    )
    return {
        "JE": (JournalEntry, "reference"),
        "INV": (Invoice, "number"),
        "CRN": (CreditNote, "number"),
        "REF": (Refund, "number"),
        "RCP": (Receipt, "number"),  # Receipts also use RCP — Payment uses Payment.number alias
        "LPO": (LandlordPayout, "number"),
        "SPY": (SupplierPayment, "number"),
        "EXP": (ExpenseClaim, "number"),
    }


_REF_PATTERN = re.compile(r"^([A-Z]+)-(\d{4})(\d{2})-(\d+)$")


def _resync_sequence(seq):
    """Bump seq.next_value to max(existing references for this period) + 1.

    No-op if the registry doesn't know about the prefix or no rows exist.
    """
    registry = _ref_models()
    pair = registry.get(seq.prefix)
    if not pair:
        return
    Model, field = pair
    pattern = f"{seq.prefix}-{seq.year}{seq.month:02d}-%"
    qs = Model._default_manager.filter(**{f"{field}__like": pattern}).values_list(field, flat=True)
    max_n = 0
    for ref in qs:
        if not ref:
            continue
        m = _REF_PATTERN.match(ref)
        if m and m.group(1) == seq.prefix:
            try:
                n = int(m.group(4))
                if n > max_n:
                    max_n = n
            except (TypeError, ValueError):
                continue
    if seq.next_value <= max_n:
        seq.next_value = max_n + 1
        seq.save(update_fields=["next_value"])


def allocate_number(prefix, *, now=None):
    """Atomically allocate the next number for `prefix` in the current
    year-month. Returns the formatted string, e.g. `INV-202604-00042`.

    The caller must be inside a transaction (or we open one) so that a
    failed downstream save does NOT burn a sequence value — Postgres
    rolls the increment back on rollback.

    Resilient against drift: if the resulting reference would collide with
    an already-existing row (because some legacy code path created rows
    without going through this allocator), the sequence is auto-resynced
    against the live max and we retry once.
    """
    now = now or timezone.localtime()
    year = now.year
    month = now.month
    pad = PAD_WIDTH.get(prefix, 5)
    fmt = lambda n: f"{prefix}-{year}{month:02d}-{n:0{pad}d}"

    for attempt in (1, 2):
        with transaction.atomic():
            seq, _ = NumberSequence.objects.select_for_update().get_or_create(
                prefix=prefix, year=year, month=month, defaults={"next_value": 1},
            )
            # On retry, repair drift before allocating
            if attempt == 2:
                _resync_sequence(seq)
            allocated = seq.next_value
            candidate = fmt(allocated)
            registry = _ref_models()
            collide = False
            if prefix in registry:
                Model, field = registry[prefix]
                collide = Model._default_manager.filter(**{field: candidate}).exists()
            if collide:
                # Sync up + try again
                _resync_sequence(seq)
                continue
            seq.next_value = allocated + 1
            seq.save(update_fields=["next_value"])
            return candidate

    # Final fallback — let any IntegrityError raise to the caller
    return fmt(allocated)
