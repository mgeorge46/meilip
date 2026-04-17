"""Accounting utilities — routing, posting helpers."""

from django.core.exceptions import ObjectDoesNotExist

from .models import Account


# System codes — stable identifiers referenced by code. See seed migration.
SYS_CASH = "CASH_ON_HAND"
SYS_AR_TENANTS = "AR_TENANT_BALANCES"
SYS_RENT_INCOME = "RENT_INCOME"
SYS_COMMISSION_INCOME = "COMMISSION_INCOME"
SYS_LANDLORD_PAYABLE = "LANDLORD_PAYABLE"
SYS_SECURITY_DEPOSIT_HELD = "SECURITY_DEPOSIT_HELD"
SYS_SECURITY_DEPOSIT_REFUNDABLE = "SECURITY_DEPOSIT_REFUNDABLE"
SYS_TAX_PAYABLE = "TAX_PAYABLE"
SYS_TENANT_ADVANCE_HELD_MANAGED = "TENANT_ADVANCE_HELD_MANAGED"
SYS_TENANT_ADVANCE_HELD_MEILI = "TENANT_ADVANCE_HELD_MEILI"


def get_account(system_code):
    """Fetch an account by its stable system_code."""
    return Account.objects.get(system_code=system_code)


def get_advance_holding_account(house):
    """Return the correct `Tenant Advance Payments Held` account for a house.

    - Meili-owned landlord → `Tenant Advance Payments Held — Meili-Owned`
      (deferred revenue)
    - External landlord → `Tenant Advance Payments Held — Managed Properties`
      (fiduciary liability)

    Routing is automatic based on the house's effective landlord — never ask
    the employee to pick. See SPEC §20.1.
    """
    if house is None:
        raise ValueError("house is required to route advance-holding account.")
    try:
        landlord = house.effective_landlord
    except ObjectDoesNotExist:
        landlord = None
    if landlord is None:
        raise ValueError(
            f"House {house.pk} has no effective landlord — cannot route advance."
        )
    code = (
        SYS_TENANT_ADVANCE_HELD_MEILI
        if landlord.is_meili_owned
        else SYS_TENANT_ADVANCE_HELD_MANAGED
    )
    return get_account(code)
