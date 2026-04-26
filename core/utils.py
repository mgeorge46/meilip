"""Core utilities — effective-settings resolver, advance-holding-account router, CSV export."""

import csv
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse
from django.utils import timezone


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


def get_effective_setting_with_source(house, field_name):
    """Like get_effective_setting, but also returns the source: 'house', 'estate', or 'none'."""
    if house is None:
        return None, "none"
    value = getattr(house, field_name, None)
    if value is not None:
        return value, "house"
    try:
        estate = house.estate
    except ObjectDoesNotExist:
        return None, "none"
    if estate is None:
        return None, "none"
    estate_value = getattr(estate, field_name, None)
    return (estate_value, "estate") if estate_value is not None else (None, "none")


def _csv_value(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return timezone.localtime(v).strftime("%Y-%m-%d %H:%M") if timezone.is_aware(v) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return str(v)


def export_csv(rows, columns, filename):
    """Stream a CSV response.

    rows: iterable of dict/obj
    columns: list of (header, accessor) where accessor is str key/attr path OR callable(row)->value.
             Dotted paths like "tenant.full_name" are supported.
    filename: base name without extension.
    """
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}_{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow([h for h, _ in columns])

    def resolve(row, accessor):
        if callable(accessor):
            return accessor(row)
        cur = row
        for part in accessor.split("."):
            if cur is None:
                return None
            cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
            if callable(cur) and not hasattr(cur, "__self__"):
                try:
                    cur = cur()
                except Exception:
                    return None
        return cur

    for row in rows:
        writer.writerow([_csv_value(resolve(row, acc)) for _, acc in columns])
    return response
