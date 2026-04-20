"""Per-prefix per-month sequence numbers with atomic allocation.

Numbers are never reused. Voided invoices keep their number — gaps in a
prefix's sequence therefore indicate void events and are an auditable
signal. Concurrency-safe via SELECT ... FOR UPDATE on the sequence row.
"""
from django.db import models, transaction
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
}


def allocate_number(prefix, *, now=None):
    """Atomically allocate the next number for `prefix` in the current
    year-month. Returns the formatted string, e.g. `INV-202604-00042`.

    The caller must be inside a transaction (or we open one) so that a
    failed downstream save does NOT burn a sequence value — Postgres
    rolls the increment back on rollback.
    """
    now = now or timezone.localtime()
    year = now.year
    month = now.month
    pad = PAD_WIDTH.get(prefix, 5)
    with transaction.atomic():
        seq, _ = NumberSequence.objects.select_for_update().get_or_create(
            prefix=prefix, year=year, month=month, defaults={"next_value": 1}
        )
        allocated = seq.next_value
        seq.next_value = allocated + 1
        seq.save(update_fields=["next_value"])
    return f"{prefix}-{year}{month:02d}-{allocated:0{pad}d}"
