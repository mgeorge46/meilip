"""Core utilities — effective-settings resolver, advance-holding-account router."""

from django.core.exceptions import ObjectDoesNotExist


def get_effective_setting(house, field_name):
    """Return the effective value of `field_name` for a house.

    House-level value wins if non-null; otherwise fall back to the estate.
    Returns None if neither level has a value set.

    Example:
        billing_cycle = get_effective_setting(house, 'billing_cycle')
    """
    if house is None:
        return None
    value = getattr(house, field_name, None)
    if value is not None:
        return value
    try:
        estate = house.estate
    except ObjectDoesNotExist:
        return None
    return getattr(estate, field_name, None) if estate else None
