"""Billing exceptions — surfaced to users when protected financial records
are touched via URL/ORM/admin regardless of UI hiding."""


class ProtectedFinancialRecord(Exception):
    """Raised when code attempts to hard-delete or destructively modify an
    invoice, payment, credit note, refund, journal, or receipt in a state
    that the spec prohibits (Issued/Paid/Voided/Overdue invoices, any
    posted receipt or journal)."""


class SelfApprovalBlocked(Exception):
    """Raised when a user attempts to approve their own submission. Always
    blocked per SPEC §16 maker-checker rules, without exception."""


class TrustedBypassBlocked(Exception):
    """Raised when a trusted employee attempts to self-approve a void,
    credit-note or refund. Trusted-bypass applies only to ordinary payments
    and ad-hoc charges; destructive financial actions always need a checker."""


class InvalidInvoiceTransition(Exception):
    """Raised on illegal invoice state machine transitions."""


class CreditNoteExceedsInvoice(Exception):
    """Credit-note amount would exceed the original invoice's outstanding
    balance less any credit already applied."""


class InvoiceGenerationPaused(Exception):
    """TenantHouse has invoice generation paused/stopped."""
