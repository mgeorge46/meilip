"""API key model for per-bank/provider payment webhook authentication.

Each integrated bank / mobile-money aggregator is issued its own ApiKey,
bound to a BankAccount (so the deposit can be routed to the right ledger
account) and optionally scoped to source IPs. Keys are stored hashed
using Django's password hashers so a DB leak does not expose plaintext.
"""
from __future__ import annotations

import secrets
from hashlib import sha256

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


def _generate_raw_key() -> str:
    """Return a URL-safe 48-byte key prefixed with `mk_` for readability."""
    return "mk_" + secrets.token_urlsafe(48)


def _hash_key(raw: str) -> str:
    """Deterministic SHA-256 hash — allows O(1) lookup by prefix+hash."""
    return sha256(raw.encode("utf-8")).hexdigest()


class ApiKey(models.Model):
    """One key per integrating bank/provider.

    Lookup flow on an inbound request:
      1. Split header value into (prefix, hash)
      2. Select row by `key_prefix` (indexed, unique)
      3. Constant-time compare `hashed_key` against sha256(raw)
      4. Touch `last_used_at`, enforce `is_active` + `revoked_at`
    """

    name = models.CharField(
        max_length=120,
        help_text="Human label for this integration (e.g. 'Stanbic Payments Webhook').",
    )
    # Short non-secret prefix extracted from the raw key. 12 chars is enough
    # to index but not enough to brute-force the remainder.
    key_prefix = models.CharField(max_length=16, unique=True, db_index=True)
    hashed_key = models.CharField(max_length=128)

    bank_account = models.ForeignKey(
        "accounting.BankAccount",
        on_delete=models.PROTECT,
        related_name="api_keys",
        help_text="All payments authenticated with this key deposit to this bank account.",
    )
    allowed_ips = models.TextField(
        blank=True,
        help_text="Optional comma-separated allowlist of source IPs. Empty = any.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.key_prefix}…)"

    # --- Factory ------------------------------------------------------------
    @classmethod
    def issue(cls, *, name, bank_account, allowed_ips="", created_by=None):
        """Create + return `(api_key_instance, raw_key_string)`.

        The raw string is only returned here — it is NEVER persisted.
        Caller must relay it to the integrating party out of band.
        """
        raw = _generate_raw_key()
        prefix = raw[:12]
        instance = cls.objects.create(
            name=name,
            key_prefix=prefix,
            hashed_key=_hash_key(raw),
            bank_account=bank_account,
            allowed_ips=allowed_ips,
            created_by=created_by,
        )
        return instance, raw

    # --- Verification helper -----------------------------------------------
    def verify(self, raw: str) -> bool:
        import hmac
        return hmac.compare_digest(self.hashed_key, _hash_key(raw))

    def mark_used(self):
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    def revoke(self, *, user=None):
        self.is_active = False
        self.revoked_at = timezone.now()
        self.revoked_by = user
        self.save(update_fields=["is_active", "revoked_at", "revoked_by"])

    def ip_allowed(self, ip: str) -> bool:
        if not self.allowed_ips.strip():
            return True
        allowed = {x.strip() for x in self.allowed_ips.split(",") if x.strip()}
        return ip in allowed


class WebhookEvent(models.Model):
    """Inbound webhook audit trail — every call hits this table first.

    Idempotency is enforced by `(api_key, transaction_id)` uniqueness: a
    duplicate post returns the original response instead of double-crediting.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        ACCEPTED = "ACCEPTED", "Accepted"
        DUPLICATE = "DUPLICATE", "Duplicate"
        UNMATCHED = "UNMATCHED", "Unmatched payer"
        INVALID = "INVALID", "Invalid payload"
        ERROR = "ERROR", "Error"

    api_key = models.ForeignKey(
        ApiKey, on_delete=models.PROTECT, related_name="events",
    )
    transaction_id = models.CharField(max_length=128, db_index=True)
    source_name = models.CharField(max_length=120, blank=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    payload = models.JSONField()
    status = models.CharField(max_length=16, choices=Status.choices)
    response_code = models.PositiveSmallIntegerField(default=202)
    response_body = models.JSONField(default=dict, blank=True)
    error_detail = models.TextField(blank=True)
    payment = models.ForeignKey(
        "billing.Payment", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="webhook_events",
    )
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-received_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["api_key", "transaction_id"],
                name="uniq_api_key_transaction_id",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "received_at"]),
        ]

    def __str__(self):
        return f"{self.transaction_id} [{self.status}]"
